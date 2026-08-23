"""The compiler's job is transcription, not judgement.

It runs outside the trust boundary — the user confirms its output before it
becomes authority — so what these tests pin down is narrow and specific: it
must not invent a bound, and it must hand the engine a contract that means
what the sentence meant.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bounded_mandate.compiler import Compiled, MandateDraft, compile_mandate

HOME = frozenset({"12 Nandidurga Rd, Bengaluru"})
SAID = "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"


def stub(**fields) -> object:
    """A client that returns one fixed draft. Keeps the suite offline."""
    draft = MandateDraft(
        per_txn_max_paise=fields.get("per_txn_max_paise", 200_000),
        merchants=fields.get("merchants", ["instamart"]),
        categories=fields.get("categories", ["groceries"]),
        cadence_days=fields.get("cadence_days", 4),
    )
    parse = lambda **_: SimpleNamespace(parsed_output=draft)  # noqa: E731
    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def run(client) -> Compiled:
    return compile_mandate(SAID, mandate_id="mdt_1", delivery_addresses=HOME, client=client)


def test_sentence_becomes_an_enforceable_contract():
    policy = run(stub()).policy

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


def test_a_complete_rule_reports_nothing_missing():
    assert run(stub()).missing == ()
