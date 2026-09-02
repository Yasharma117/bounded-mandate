"""The one place that knows how to talk to a model.

Provider is NVIDIA NIM behind its OpenAI-compatible endpoint. The entire
binding is two environment variables, so swapping model — or swapping provider
entirely, to anything that speaks the OpenAI shape — is config, not code.

Output is constrained with NIM's `guided_json`, which enforces a JSON schema at
the decoding level (xgrammar). NVIDIA recommends it over
`response_format={"type": "json_object"}`, which permits any valid JSON
including `{}`. Callers still validate what comes back: guided decoding
guarantees the shape, never the sense.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

BASE_URL = os.environ.get("BM_LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
MODEL = os.environ.get("BM_LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# An instruct model, not a reasoning one. A four-field extraction does not need
# a thinking trace, and on a live demo that trace is just dead air.
# Swap with BM_LLM_MODEL — deepseek-ai/deepseek-v4-pro and z-ai/glm-5.2 are the
# alternates if the Phase 3 agent needs stronger tool-calling.


def default_client() -> Any:
    from openai import OpenAI

    return OpenAI(
        base_url=BASE_URL,
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
        # The SDK already retries 429 and 5xx with exponential backoff — free-tier
        # NIM rate-limits are the expected failure, so give it more attempts than
        # the default 2 rather than writing a retry loop of our own.
        #
        # It does not retry 404, and `call_model` below exists because NIM uses
        # 404 for a *transient* failure. See there.
        max_retries=5,
        timeout=30.0,
    )


#: How many times to re-send a request NIM dropped, and how long to wait first.
RETRY_STATUSES = frozenset({404, 503})
MAX_ATTEMPTS = 4


def call_model(client: Any, **kwargs: Any) -> Any:
    """One chat completion, retried when NIM drops it on the floor.

    **Retrying a 404 is normally wrong**, and it is right here for a measured
    reason. NIM answers a request its routing layer cannot place with

        404 {"detail": "Function '9b96341b-…': Not found for account '…'"}

    for a model that is entitled, listed by `/v1/models`, and answers the very
    next request. Measured on `nemotron-3-super-120b-a12b`: eight of ten plain
    calls succeeded and the failures were 404 and 503, seconds apart, with no
    change to the request. The OpenAI SDK retries 5xx and treats 4xx as
    permanent, so the 503s recovered silently and a 404 ended the run — the app
    said "the agent could not run: Error code: 404" and stopped, in the middle
    of a demo, for a model that was working.

    A genuinely missing model still surfaces, a couple of seconds later, which
    is the right trade: the wrong-model case is rare and diagnosed once, and the
    dropped-request case happens roughly one run in five.

    Jittered, because a retry storm from several callers landing together is how
    a flaky endpoint is made flakier.
    """
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised below if not transient
            status = getattr(exc, "status_code", None)
            if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS - 1:
                raise
            last = exc
            time.sleep((0.4 * 2**attempt) + random.uniform(0, 0.3))
    raise last  # unreachable; the loop either returns or raises


def _json_object(text: str) -> dict[str, Any]:
    """Parse the JSON object out of a completion.

    `guided_json` should make the whole body a bare object, but a model that
    ignores it (or a reasoning model that prefixes a trace) would otherwise take
    the caller down with a decode error. Slice to the outermost braces first.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in completion: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def complete_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """One constrained completion. Raises on transport failure — callers decide."""
    client = client or default_client()
    completion = call_model(
        client,
        model=model or MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,  # the recorded demo has to run the same way twice
        extra_body={
            "guided_json": schema,
            # Nemotron 3 Super is a hybrid reasoner and thinks by default, which
            # cost 8s on a four-field extraction. Neither job here benefits from
            # a reasoning trace, and a live demo feels every second of it.
            # Harmlessly ignored by providers that do not take the kwarg.
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    return _json_object(completion.choices[0].message.content)
