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
from bounded_mandate.engine import PROBE_THRESHOLD, PROBE_WINDOW
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


# --- a pattern of refusals is itself a finding -------------------------------


def _deny_once(policies, ledger, n, now):
    """One refused proposal: a cart with a smartwatch in it, reported as groceries."""
    cart = Cart(
        cart_id=f"probe_{n}",
        merchant="instamart",
        items=(CartItem("Smartwatch", 1_500_000, "electronics"),),
        delivery_address=HOME,
    )
    return decide(
        Proposal("mdt_1", cart.cart_id, 185_000),
        policies=policies,
        adapter=merchant_holding(cart),
        ledger=ledger,
        now=now,
    )


def test_a_single_refusal_is_not_a_pattern(policies, ledger):
    assert _deny_once(policies, ledger, 1, NOW).verdict is Verdict.DENY
    clean = run(
        Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger
    )
    assert clean.verdict is Verdict.ALLOW
    assert clean.reason_code == "ok.in_policy"


def test_repeated_refusals_stop_the_silent_path(policies, ledger):
    """After probing, even a clean basket needs a human. That is the point."""
    for n in range(PROBE_THRESHOLD):
        assert _deny_once(policies, ledger, n, NOW).verdict is Verdict.DENY

    clean = run(
        Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger
    )

    assert clean.verdict is Verdict.ESCALATE
    assert clean.reason_code == "agent.probing"


def test_probing_is_surfaced_on_the_refusal_itself(policies, ledger):
    for n in range(PROBE_THRESHOLD):
        _deny_once(policies, ledger, n, NOW)

    another = _deny_once(policies, ledger, 99, NOW)

    assert another.verdict is Verdict.DENY  # still denied, and now also flagged
    assert "agent.probing" in another.reason_code


def test_old_refusals_do_not_count(policies, ledger):
    """Probing is a burst. Yesterday's mistakes are not evidence of one."""
    stale = NOW - PROBE_WINDOW - timedelta(minutes=1)
    for n in range(PROBE_THRESHOLD + 2):
        _deny_once(policies, ledger, n, stale)

    clean = run(
        Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger
    )
    assert clean.verdict is Verdict.ALLOW


def test_escalations_are_not_counted_as_probing(policies, ledger):
    """An over-cap basket is a boundary, not an attack. Three do not make a pattern."""
    for n in range(PROBE_THRESHOLD + 1):
        over = Cart(
            cart_id=f"over_{n}",
            merchant="instamart",
            items=(CartItem("Caviar", 900_000, "groceries"),),
            delivery_address=HOME,
        )
        d = run(Proposal("mdt_1", over.cart_id, 900_000), policies, merchant_holding(over), ledger)
        assert d.verdict is Verdict.ESCALATE

    clean = run(
        Proposal("mdt_1", "cart_1", 185_000), policies, merchant_holding(groceries()), ledger
    )
    assert clean.verdict is Verdict.ALLOW


def test_reason_prose_is_written_for_a_person_not_a_form(policy, policies, ledger):
    """`1 item(s)` is the sound a form makes. These strings are read aloud by a
    voice agent and printed on a card, so they have to be sentences."""
    cart = Cart(
        cart_id="cart_1",
        merchant="instamart",
        items=(CartItem("Bluetooth earbuds", 40_000, "electronics"),),
        delivery_address=HOME,
    )
    decision = decide(
        Proposal(policy.mandate_id, cart.cart_id, 40_000),
        policies=policies,
        adapter=merchant_holding(cart),
        ledger=ledger,
        now=NOW,
    )
    prose = " ".join(reason.detail for reason in decision.reasons)
    assert "(s)" not in prose
    assert "1 item outside your scope" in prose


# --- the window counts money, not opinions -----------------------------------
#
# An ALLOW whose order was never created spent nothing. Counting it as a charge
# meant a gateway hiccup consumed the user's cadence: the engine said yes, no
# order existed, and the next attempt was refused for a purchase that never
# happened. A window of one, so each of these proves something.


def rail_failed(ledger, decision):
    """What `_settle` writes when the gateway will not answer."""
    ledger.append(
        {
            "event": "RAIL_FAILED",
            "idempotency_key": decision.idempotency_key,
            "cart_id": decision.cart_id,
            "total_paise": decision.total_paise,
            "detail": "razorpay is not answering",
        }
    )


def test_an_allow_the_rail_refused_does_not_spend_the_window(policy, ledger):
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries(), groceries(cart_id="cart_2"))

    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert first.verdict is Verdict.ALLOW
    rail_failed(ledger, first)

    # A different basket, in a window that permits one order. Nothing was
    # bought, so nothing is exceeded.
    second = run(Proposal("mdt_1", "cart_2", 185_000), policies, adapter, ledger)
    assert second.verdict is Verdict.ALLOW, second.reason_code
    assert "frequency.exceeded" not in second.reason_code


def test_retrying_a_cart_the_rail_refused_is_not_a_duplicate(policy, ledger):
    """`duplicate.suppressed` exists to stop one cart being authorised twice.
    A cart that was allowed and never charged has been authorised zero times."""
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries())

    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    rail_failed(ledger, first)

    retry = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert retry.verdict is Verdict.ALLOW, retry.reason_code
    assert "duplicate.suppressed" not in retry.reason_code


def test_a_settled_retry_restores_the_key_as_charged(policy, ledger):
    """A recovered payment must remain charged after its earlier rail failure."""
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries())

    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    rail_failed(ledger, first)
    retry = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    ledger.append(
        {
            "event": "SETTLED",
            "idempotency_key": retry.idempotency_key,
            "cart_id": retry.cart_id,
        }
    )

    again = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert "duplicate.suppressed" in again.reason_code


def test_an_allow_that_did_reach_the_rail_still_spends_the_window(policy, ledger):
    """The other half, and the one that matters: discounting a failure must not
    become discounting a purchase. Same shape as the test above, without the
    `RAIL_FAILED` — and the answer has to flip."""
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries(), groceries(cart_id="cart_2"))

    run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)

    second = run(Proposal("mdt_1", "cart_2", 185_000), policies, adapter, ledger)
    assert "frequency.exceeded" in second.reason_code


def test_a_retry_that_did_reach_the_rail_stops_being_discounted(policy, ledger):
    """The failure is discounted; the retry that worked must not be.

    A retry of the same cart in the same window is the same idempotency key by
    construction, so a `RAIL_FAILED` that vetoed the *key* vetoed every later
    ALLOW under it too — and nothing ever lifted the veto. Measured before this:
    allow, rail fails, retry succeeds and is paid, and a third proposal for that
    same paid basket still came back ALLOW while the charge that did go through
    never counted against a window of one. Two ways to spend the same money.

    So a failure cancels the attempt it followed, and only that one.
    """
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries(), groceries(cart_id="cart_2"))

    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    rail_failed(ledger, first)

    # The retry reaches the rail. No `RAIL_FAILED` follows it, so it is a charge.
    retry = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert retry.verdict is Verdict.ALLOW, retry.reason_code

    third = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert "duplicate.suppressed" in third.reason_code, "a paid cart was authorised again"

    other = run(Proposal("mdt_1", "cart_2", 185_000), policies, adapter, ledger)
    assert "frequency.exceeded" in other.reason_code, "the paid retry never spent the window"


def test_a_second_outage_on_the_same_cart_is_still_discounted(policy, ledger):
    """Cancelling per attempt has to survive more than one attempt: two failures
    in a row are two charges that did not happen, not one."""
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries(), groceries(cart_id="cart_2"))

    for _ in range(2):
        attempt = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
        assert attempt.verdict is Verdict.ALLOW, attempt.reason_code
        rail_failed(ledger, attempt)

    other = run(Proposal("mdt_1", "cart_2", 185_000), policies, adapter, ledger)
    assert "frequency.exceeded" not in other.reason_code


def test_a_settlement_for_another_mandate_does_not_spend_this_one(policy, ledger):
    """The mirror of the test below, and the one the settled-outcome branch
    needs. `SETTLED` carries no mandate id, so it is read before the mandate
    filter — which meant any settlement carrying an idempotency key counted as a
    charge against every mandate's window at once. A settlement may only confirm
    a key this mandate was already seen to allow."""
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries())

    ledger.append(
        {
            "event": "SETTLED",
            "idempotency_key": "a_key_belonging_to_something_else",
            "razorpay_payment_id": "pay_elsewhere",
            "grant_id": "grant_elsewhere",
        }
    )

    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert "frequency.exceeded" not in first.reason_code, first.reason_code


def test_the_full_retry_sequence_leaves_one_charge(policy, ledger):
    """ALLOW -> RAIL_FAILED -> ALLOW -> SETTLED, end to end.

    The retry's own ALLOW is what makes the key count again; the settlement is
    inert here and the test says so deliberately. Reading `SETTLED` is the
    obvious thing to reach for and it cannot work — the one writer records a
    grant id and no idempotency key, and a grant whose rail failed hands out no
    checkout, so no settlement can follow a failure at all. Pinned because the
    next person to look at this will have the same idea.
    """
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries(), groceries(cart_id="cart_2"))

    first = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    rail_failed(ledger, first)
    retry = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert retry.verdict is Verdict.ALLOW, retry.reason_code
    ledger.append(
        {
            "event": "SETTLED",
            "idempotency_key": retry.idempotency_key,
            "razorpay_payment_id": "pay_ok",
            "grant_id": "grant_ok",
        }
    )

    third = run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    assert "duplicate.suppressed" in third.reason_code
    other = run(Proposal("mdt_1", "cart_2", 185_000), policies, adapter, ledger)
    assert "frequency.exceeded" in other.reason_code


def test_a_rail_failure_for_another_mandate_does_not_free_this_one(policy, ledger):
    """The discount is keyed by idempotency key, which is derived from the
    mandate — so one mandate's outage cannot buy another mandate a window."""
    policies = {"mdt_1": replace(policy, max_charges_per_window=1)}
    adapter = merchant_holding(groceries(), groceries(cart_id="cart_2"))

    run(Proposal("mdt_1", "cart_1", 185_000), policies, adapter, ledger)
    ledger.append(
        {
            "event": "RAIL_FAILED",
            "idempotency_key": "a_key_belonging_to_something_else",
            "cart_id": "cart_9",
            "total_paise": 185_000,
            "detail": "razorpay is not answering",
        }
    )

    second = run(Proposal("mdt_1", "cart_2", 185_000), policies, adapter, ledger)
    assert "frequency.exceeded" in second.reason_code
