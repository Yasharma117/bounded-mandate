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
