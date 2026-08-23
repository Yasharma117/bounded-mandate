"""The engine's contract, stated as failures.

Each test names a way the layer could be wrong. The two that matter most are
`test_lying_agent_is_caught` (Layer 0) and `test_semantic_layer_cannot_approve`
(Layer 2's one-directionality) — those are the security properties the whole
product rests on.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from bounded_mandate import Cart, CartItem, MandateStatus, Proposal, Verdict, decide
from tests.conftest import HOME, NOW, groceries, merchant_holding


def run(proposal, policies, adapter, ledger, **kw):
    return decide(proposal, policies=policies, adapter=adapter, ledger=ledger, now=NOW, **kw)


# --- the silent path ---------------------------------------------------------


def test_in_policy_cart_is_allowed_silently(policies, ledger):
    cart = groceries()
    decision = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(cart), ledger)

    assert decision.verdict is Verdict.ALLOW
    assert decision.reason_code == "ok.in_policy"
    assert decision.total_paise == 185_000


def test_every_path_writes_one_ledger_entry(policies, ledger):
    run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger)
    run(Proposal("mdt_1", "nope", 1), policies, merchant_holding(), ledger)

    assert ledger.verify() == 2


# --- Layer 0: proposal integrity (the hero) ----------------------------------


def test_lying_agent_is_caught(policies, ledger):
    """The agent reports ₹1,850 over a cart that really holds ₹1,850 + ₹15,000."""
    real = Cart(
        cart_id="cart_1",
        merchant="instamart",
        items=(
            CartItem("12 grocery items", 185_000, "groceries"),
            CartItem("Smartwatch", 1_500_000, "electronics"),
        ),
        delivery_address=HOME,
    )
    decision = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(real), ledger)

    assert decision.verdict is Verdict.DENY
    assert "provenance.total_mismatch" in decision.reason_code
    # The engine evaluated the cart it fetched, not the one it was told about.
    assert decision.total_paise == 1_685_000


def test_unknown_cart_is_denied(policies, ledger):
    decision = run(Proposal("mdt_1", "ghost", 185_000), policies, merchant_holding(), ledger)
    assert decision.verdict is Verdict.DENY
    assert decision.reason_code == "provenance.cart_not_found"


def test_policy_is_never_taken_from_the_proposal(ledger):
    """An agent naming a mandate the engine does not hold gets nothing."""
    decision = run(
        Proposal("mdt_forged", "cart_1", 185_000), {}, merchant_holding(groceries()), ledger
    )
    assert decision.verdict is Verdict.DENY
    assert decision.reason_code == "mandate.unknown"


# --- Layer 1: hard policy ----------------------------------------------------


def test_cap_breach_and_off_category_surface_together(policies, ledger):
    """Exhibit B: two independent flags on one escalation screen."""
    cart = Cart(
        cart_id="cart_1",
        merchant="instamart",
        items=(
            CartItem("12 grocery items", 185_000, "groceries"),
            CartItem("Bluetooth earbuds", 40_000, "electronics"),
            CartItem("Phone case", 15_000, "accessories"),
        ),
        delivery_address=HOME,
    )
    decision = run(Proposal("mdt_1", "cart_1", 240_000), policies, merchant_holding(cart), ledger)

    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "category.not_allowed+cap.exceeded"


@pytest.mark.parametrize(
    "status,code",
    [(MandateStatus.REVOKED, "mandate.revoked"), (MandateStatus.PAUSED, "mandate.paused")],
)
def test_dead_mandate_denies(policy, ledger, status, code):
    policies = {"mdt_1": replace(policy, status=status)}
    decision = run(
        Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger
    )
    assert decision.verdict is Verdict.DENY
    assert decision.reason_code == code


def test_expired_mandate_denies(policy, ledger):
    policies = {"mdt_1": replace(policy, expires_at=NOW - timedelta(seconds=1))}
    decision = run(
        Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger
    )
    assert decision.reason_code == "mandate.expired"


def test_wrong_merchant_escalates(policies, ledger):
    cart = replace(groceries(), merchant="blinkit")
    decision = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(cart), ledger)
    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "merchant.not_allowed"


def test_stranger_address_escalates_even_inside_every_other_bound(policies, ledger):
    """₹1,850 of ordinary groceries, correct merchant — shipped somewhere else."""
    cart = replace(groceries(), delivery_address="Someone else's flat")
    decision = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(cart), ledger)
    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "delivery.unknown_address"


def test_unclassifiable_item_clarifies_rather_than_guessing(policies, ledger):
    cart = replace(groceries(), items=(CartItem("Whey protein 1kg", 185_000, ""),))
    decision = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(cart), ledger)
    assert decision.verdict is Verdict.CLARIFY
    assert decision.reason_code == "category.unknown"


def test_clarify_never_outranks_a_boundary_breach(policies, ledger):
    """Ambiguity and a violation are different acts; the violation wins."""
    cart = replace(
        groceries(),
        items=(CartItem("Whey protein 1kg", 185_000, ""), CartItem("Coffee", 60_000, "groceries")),
    )
    decision = run(Proposal("mdt_1", "cart_1", 245_000), policies, merchant_holding(cart), ledger)
    assert decision.verdict is Verdict.ESCALATE
    assert "category.unknown" in decision.reason_code


def test_frequency_ceiling_escalates(policies, ledger):
    for n in (1, 2):
        cart = groceries(cart_id=f"cart_{n}")
        prior = run(
            Proposal("mdt_1", cart.cart_id, 185_000), policies, merchant_holding(cart), ledger
        )
        assert prior.verdict is Verdict.ALLOW

    third = groceries(cart_id="cart_3")
    decision = run(Proposal("mdt_1", "cart_3", 185_000), policies, merchant_holding(third), ledger)
    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "frequency.exceeded"


# --- idempotency -------------------------------------------------------------


def test_same_cart_twice_in_a_window_authorises_once(policies, ledger):
    merchant = merchant_holding(groceries())
    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant, ledger)
    second = run(Proposal("mdt_1", "cart_1", 185_000), policies, merchant, ledger)

    assert first.verdict is Verdict.ALLOW
    assert second.verdict is Verdict.DENY
    assert "duplicate.suppressed" in second.reason_code
    assert first.idempotency_key == second.idempotency_key


# --- Layer 2: the model, one-directional -------------------------------------


def test_semantic_layer_can_raise_suspicion(policies, ledger):
    def suspicious(cart, policy):
        return ["Basket does not look like a weekly grocery run."]

    decision = run(
        Proposal("mdt_1", "cart_1", 185_000),
        policies,
        merchant_holding(groceries()),
        ledger,
        semantic_check=suspicious,
    )
    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "intent.mismatch"


def test_semantic_layer_cannot_approve(policies, ledger):
    """A fully compromised Layer 2 cannot widen authority. It has no yes to give."""
    cart = replace(groceries(), items=(CartItem("Caviar", 900_000, "groceries"),))

    def compromised(cart, policy):
        return []  # the model is owned and says "all clear"

    decision = run(
        Proposal("mdt_1", "cart_1", 900_000),
        policies,
        merchant_holding(cart),
        ledger,
        semantic_check=compromised,
    )
    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "cap.exceeded"


def test_layer_2_outage_is_recorded_not_hidden(policies, ledger):
    """Fail open — but a decision made with a layer down must say so."""

    def unreachable(cart, policy):
        raise ConnectionError("NIM unreachable")

    decision = run(
        Proposal("mdt_1", "cart_1", 185_000),
        policies,
        merchant_holding(groceries()),
        ledger,
        semantic_check=unreachable,
    )

    assert decision.verdict is Verdict.ALLOW
    assert decision.reason_code == "semantic.unavailable"


def test_layer_2_outage_does_not_weaken_layer_1(policies, ledger):
    """The hard bounds are deterministic and hold on their own."""

    def unreachable(cart, policy):
        raise ConnectionError("NIM unreachable")

    over_cap = replace(groceries(), items=(CartItem("Caviar", 900_000, "groceries"),))
    decision = run(
        Proposal("mdt_1", "cart_1", 900_000),
        policies,
        merchant_holding(over_cap),
        ledger,
        semantic_check=unreachable,
    )

    # Both facts recorded: the bound was breached, and Layer 2 was down for it.
    assert decision.verdict is Verdict.ESCALATE
    assert decision.reason_code == "cap.exceeded+semantic.unavailable"
