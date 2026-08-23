"""The compiler's job is transcription, not judgement.

It runs outside the trust boundary — the user confirms its output before it
becomes authority — so what these tests pin down is narrow: it must not invent
a bound, it must hand the engine a contract that means what the sentence meant,
and a provider outage must not be able to break the recorded demo.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bounded_mandate.compiler import Compiled, MandateDraft, compile_mandate, render

HOME = frozenset({"12 Nandidurga Rd, Bengaluru"})
SAID = "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"
GOOD = {
    "per_txn_max_paise": 200_000,
    "merchants": ["instamart"],
    "categories": ["groceries"],
    "cadence_days": 4,
}


def stub(content: str | None = None, *, raises: Exception | None = None, **override):
    """An OpenAI-shaped client. Keeps the suite offline."""
    if content is None:
        content = json.dumps({**GOOD, **override})
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.calls = calls
    return client


def run(client) -> Compiled:
    return compile_mandate(SAID, mandate_id="mdt_1", delivery_addresses=HOME, client=client)


# --- the model path ----------------------------------------------------------


def test_sentence_becomes_an_enforceable_contract():
    compiled = run(stub())
    policy = compiled.policy

    assert compiled.source == "model"
    assert policy is not None
    assert policy.per_txn_max_paise == 200_000  # ₹2,000, in paise
    assert policy.merchants == frozenset({"instamart"})
    assert policy.categories == frozenset({"groceries"})


def test_cadence_compiles_to_a_frequency_ceiling():
    """'every 4 days' is one authorised order per 4-day window."""
    policy = run(stub(cadence_days=4)).policy
    assert (policy.max_charges_per_window, policy.window_days) == (1, 4)


def test_delivery_addresses_come_from_the_account_not_the_sentence():
    policy = run(stub()).policy
    assert policy.delivery_addresses == HOME
    assert "delivery" not in " ".join(MandateDraft.model_fields)


@pytest.mark.parametrize(
    "gap",
    [{"per_txn_max_paise": None}, {"merchants": []}, {"categories": []}, {"cadence_days": None}],
)
def test_an_unstated_bound_is_never_guessed(gap):
    compiled = run(stub(**gap))

    assert compiled.policy is None, "an incomplete rule must not compile to authority"
    assert compiled.missing == tuple(gap)


# --- the offline fallback ----------------------------------------------------


def test_provider_outage_still_compiles_the_demo_sentence():
    """A NIM hiccup must not be able to break a live recording."""
    compiled = run(stub(raises=RuntimeError("429 Too Many Requests")))

    assert compiled.source == "fallback"
    assert compiled.policy is not None
    assert compiled.policy.per_txn_max_paise == 200_000
    assert compiled.policy.merchants == frozenset({"instamart"})
    assert compiled.policy.categories == frozenset({"groceries"})
    assert compiled.policy.window_days == 4


def test_unusable_completion_falls_back_rather_than_crashing():
    assert run(stub("I'm sorry, I can't help with that.")).source == "fallback"


def test_schema_violating_completion_falls_back():
    """Shape is guaranteed by guided_json; sense is not. Validate anyway."""
    assert run(stub(json.dumps({"per_txn_max_paise": "two thousand"}))).source == "fallback"


def test_the_fallback_does_not_guess_either():
    """Same rule binds it: no stated bound, no authority."""
    vague = compile_mandate(
        "order groceries from instamart whenever we run low",
        mandate_id="mdt_1",
        delivery_addresses=HOME,
        client=stub(raises=RuntimeError("down")),
    )
    assert vague.policy is None
    assert vague.missing == ("per_txn_max_paise", "cadence_days")


@pytest.mark.parametrize(
    "said,days",
    [("weekly under ₹500 groceries at zepto", 7), ("daily under ₹500 groceries at zepto", 1)],
)
def test_fallback_reads_worded_cadences(said, days):
    compiled = compile_mandate(
        said, mandate_id="m", delivery_addresses=HOME, client=stub(raises=RuntimeError("down"))
    )
    assert compiled.draft.cadence_days == days
    assert compiled.draft.per_txn_max_paise == 50_000


# --- the card ----------------------------------------------------------------


def test_render_names_which_path_produced_the_contract():
    """Never hide a fallback from whoever is watching the demo."""
    assert "[compiled by fallback]" in render(run(stub(raises=RuntimeError("down"))))
    assert "[compiled by model]" in render(run(stub()))


def test_render_asks_for_what_is_missing():
    assert "Needs an answer" in render(run(stub(cadence_days=None)))
