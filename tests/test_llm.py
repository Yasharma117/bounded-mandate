"""The provider seam. Small, because that is the point of it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bounded_mandate.llm import _json_object, complete_json

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def stub(content: str):
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.calls = calls
    return client


def test_output_is_constrained_by_guided_json():
    """The schema must reach the provider — it is what makes the shape reliable."""
    client = stub('{"ok": true}')
    complete_json("sys", "user", SCHEMA, client=client)

    assert client.calls[0]["extra_body"]["guided_json"] == SCHEMA


def test_thinking_is_off():
    """Nemotron 3 Super thinks by default: 8s vs 0.8s on a four-field extraction."""
    client = stub('{"ok": true}')
    complete_json("sys", "user", SCHEMA, client=client)

    assert client.calls[0]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_sampling_is_pinned_for_the_recorded_run():
    client = stub('{"ok": true}')
    complete_json("sys", "user", SCHEMA, client=client)
    assert client.calls[0]["temperature"] == 0


def test_a_reasoning_trace_around_the_object_does_not_break_parsing():
    """Some NIM models prefix a think block even under guided decoding."""
    noisy = '<think>The user wants a flag.</think>\n{"ok": true}\nHope that helps!'
    assert _json_object(noisy) == {"ok": True}


def test_a_completion_with_no_object_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        _json_object("I'm sorry, I can't help with that.")


# --- NIM drops requests, and a dropped request is not a missing model ---------


def test_a_transient_404_is_retried_not_surfaced():
    """**This ended runs mid-demo.** NIM answers a request its routing layer
    cannot place with 404 for a model that is entitled, listed, and answers the
    next call. The OpenAI SDK treats 4xx as permanent, so it came straight out
    as "the agent could not run: Error code: 404"."""
    from bounded_mandate.llm import call_model

    class Dropped(Exception):
        status_code = 404

    attempts = []

    class Flaky:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    attempts.append(kwargs)
                    if len(attempts) < 3:
                        raise Dropped("Function 'abc': Not found for account")
                    return "answered"

    assert call_model(Flaky(), model="m") == "answered"
    assert len(attempts) == 3, "gave up before the endpoint came back"


def test_a_real_error_is_not_retried_into_the_ground():
    """A bad request is ours to fix and no amount of asking again changes it.
    Retrying one wastes a demo's worth of seconds before saying so."""
    from bounded_mandate.llm import call_model

    class Refused(Exception):
        status_code = 400

    attempts = []

    class Strict:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    attempts.append(kwargs)
                    raise Refused("malformed tools payload")

    with pytest.raises(Refused):
        call_model(Strict(), model="m")
    assert len(attempts) == 1, "retried a permanent error"


def test_a_model_that_is_really_gone_still_surfaces():
    """The cost of retrying 404: a genuinely missing model takes a few seconds
    longer to say so. It must still say so."""
    from bounded_mandate.llm import MAX_ATTEMPTS, call_model

    class Gone(Exception):
        status_code = 404

    attempts = []

    class Missing:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    attempts.append(kwargs)
                    raise Gone("no such model")

    with pytest.raises(Gone):
        call_model(Missing(), model="m")
    assert len(attempts) == MAX_ATTEMPTS
