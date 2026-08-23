"""Layer 2 has exactly one power: to raise suspicion. These pin that down."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bounded_mandate import llm_semantic_check
from bounded_mandate.semantic import MAX_CONCERNS
from tests.conftest import groceries


def stub(concerns=None, *, raises=None):
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        body = json.dumps({"concerns": concerns if concerns is not None else []})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.calls = calls
    return client


def test_concerns_come_back_as_written(policy):
    check = llm_semantic_check(client=stub(["Basket is mostly one very expensive item."]))
    assert check(groceries(), policy) == ["Basket is mostly one very expensive item."]


def test_a_clean_basket_raises_nothing(policy):
    assert llm_semantic_check(client=stub([]))(groceries(), policy) == []


def test_concerns_are_capped_so_the_surface_stays_readable(policy):
    check = llm_semantic_check(client=stub([f"concern {n}" for n in range(10)]))
    assert len(check(groceries(), policy)) == MAX_CONCERNS


def test_blank_concerns_are_dropped(policy):
    check = llm_semantic_check(client=stub(["", "   ", "Quantities look like a slip."]))
    assert check(groceries(), policy) == ["Quantities look like a slip."]


def test_provider_failure_is_raised_not_swallowed(policy):
    """The engine decides what an outage means. This layer must not decide for it."""
    check = llm_semantic_check(client=stub(raises=ConnectionError("down")))
    with pytest.raises(ConnectionError):
        check(groceries(), policy)


def test_the_prompt_carries_the_basket_and_the_rule(policy):
    client = stub([])
    llm_semantic_check(client=client)(groceries(), policy)
    sent = client.calls[0]["messages"][1]["content"]

    assert "12 grocery items" in sent  # what the agent assembled
    assert "groceries" in sent and "instamart" in sent  # what the user authorised
    assert "₹2,000" in sent  # the cap it was working under
