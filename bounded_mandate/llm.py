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
        api_key=os.environ.get("NVIDIA_API_KEY") or os.environ.get("BM_LLM_API_KEY", ""),
        # The SDK already retries 429 and 5xx with exponential backoff — free-tier
        # NIM rate-limits are the expected failure, so give it more attempts than
        # the default 2 rather than writing a retry loop of our own.
        max_retries=5,
        timeout=30.0,
    )


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
    completion = client.chat.completions.create(
        model=model or MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,  # the recorded demo has to run the same way twice
        extra_body={"guided_json": schema},
    )
    return _json_object(completion.choices[0].message.content)
