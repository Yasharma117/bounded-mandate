"""The one-time purchase.

A standing rule that refuses a ₹15,000 smartwatch is working correctly. The
question this file answers is what happens *next* — and specifically what a
one-time approval is still not allowed to do.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bounded_mandate import web
from bounded_mandate.compiler import GRANT_TTL, GrantRefused, grant_for_cart
from bounded_mandate.engine import Cart, CartItem, MandateStatus, Proposal, Verdict, decide
from tests.conftest import HOME, NOW

WATCH = ["Smartwatch"]
WATCH_PAISE = 1_500_000


def refused(client, items=WATCH):
    """Propose something the standing rule will not have, and hand back its cart."""
    out = client.post(
        "/api/proposal", json={"items": items, "claimed_total_paise": WATCH_PAISE}
    ).json()
    return out


def grant(client, cart_id):
    return client.post("/api/mandate/one-time", json={"cart_id": cart_id})


# --- the bounds come off the cart --------------------------------------------


def test_the_grant_is_derived_from_the_basket_not_from_the_request(client):
    escalated = refused(client)
    assert escalated["verdict"] == "ESCALATE"

    bounds = grant(client, escalated["cart_id"]).json()["grant"]

    # Not "electronics up to ₹20,000 at any shop" — this basket, this price.
    assert bounds["per_txn_max_paise"] == WATCH_PAISE
    assert bounds["merchants"] == ["instamart"]
    assert bounds["cart_id"] == escalated["cart_id"]
    assert "electronics" in bounds["categories"]


def test_the_grant_expires_in_minutes_not_days(client):
    escalated = refused(client)
    bounds = grant(client, escalated["cart_id"]).json()["grant"]

    left = datetime.fromisoformat(bounds["expires_at"]) - datetime.now(UTC)
    assert timedelta(minutes=10) < left <= GRANT_TTL


def test_the_standing_rule_is_untouched(client):
    before = web.POLICIES["mdt_demo"]
    grant(client, refused(client)["cart_id"])

    assert web.POLICIES["mdt_demo"] == before, "a grant must not widen the rule it sidesteps"


# --- what a grant lets through, and what it still does not --------------------


def test_the_approved_basket_reaches_the_rail(client):
    escalated = refused(client)
    out = grant(client, escalated["cart_id"]).json()

    assert out["decision"]["verdict"] == "ALLOW"
    assert out["decision"]["order_id"] == "order_charge_1"
    assert client.gateway.charged == [WATCH_PAISE]
    assert out["pay_url"].startswith("/pay?grant=grant_")


def test_a_different_basket_under_the_same_grant_is_denied(client):
    """The substitution the user never looked at.

    Without the cart bound, a grant for a ₹15,000 smartwatch is also a grant for
    any other ₹15,000 basket at the same shop for the next fifteen minutes.
    """
    approved = refused(client)
    grant_id = grant(client, approved["cart_id"]).json()["grant"]["grant_id"]

    other = web.MARKETPLACE.create_cart(
        ["Bluetooth earbuds", "Phone case"], delivery_address=HOME, merchant="instamart"
    )
    decision = decide(
        Proposal(grant_id, other.cart_id, other.total_paise),
        policies=web.POLICIES,
        adapter=web.MARKETPLACE,
        ledger=web.LEDGER,
    )

    assert decision.verdict is Verdict.DENY
    assert "grant.other_cart" in decision.reason_code


def test_a_grant_cannot_introduce_a_delivery_address(client):
    """The one dimension a one-time approval may never widen.

    The card the user approves shows a price and a shop. It is not where anyone
    would notice a stranger's doorstep, so this is refused rather than shown.
    """
    elsewhere = web.MARKETPLACE.create_cart(
        WATCH, delivery_address="9 Somebody Else's Lane, Pune", merchant="instamart"
    )

    response = grant(client, elsewhere.cart_id)

    assert response.status_code == 403
    assert "address" in response.json()["detail"]
    assert client.gateway.charged == []


def test_an_unknown_basket_is_a_404(client):
    assert grant(client, "cart_nope").status_code == 404


# --- spent once ---------------------------------------------------------------


def test_paying_spends_the_grant(client):
    grant_id = grant(client, refused(client)["cart_id"]).json()["grant"]["grant_id"]

    settled = client.post(
        "/api/settlement/verify",
        json={
            "razorpay_order_id": "order_charge_1",
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": "sig",
        },
    )

    assert settled.status_code == 200
    assert web.POLICIES[grant_id].status is MandateStatus.REVOKED
    assert client.get(f"/api/grant/{grant_id}").json()["state"] == "paid"


def test_a_spent_grant_authorises_nothing_further(client):
    approved = refused(client)
    grant_id = grant(client, approved["cart_id"]).json()["grant"]["grant_id"]
    client.post(
        "/api/settlement/verify",
        json={
            "razorpay_order_id": "order_charge_1",
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": "sig",
        },
    )

    again = decide(
        Proposal(grant_id, approved["cart_id"], WATCH_PAISE),
        policies=web.POLICIES,
        adapter=web.MARKETPLACE,
        ledger=web.LEDGER,
    )

    assert again.verdict is Verdict.DENY
    assert "mandate.revoked" in again.reason_code


def test_approving_the_same_basket_twice_mints_one_grant(client):
    cart_id = refused(client)["cart_id"]

    first = grant(client, cart_id).json()
    second = grant(client, cart_id).json()

    assert first["grant"]["grant_id"] == second["grant"]["grant_id"]
    assert client.gateway.charged == [WATCH_PAISE], "one basket, one order"


def test_a_lapsed_grant_hands_out_no_checkout_and_authorises_nothing(client):
    approved = refused(client)
    grant_id = grant(client, approved["cart_id"]).json()["grant"]["grant_id"]

    lapsed = replace(
        web.GRANTS[grant_id].policy, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    web.GRANTS[grant_id].policy = lapsed
    web.POLICIES[grant_id] = lapsed

    out = client.get(f"/api/grant/{grant_id}").json()
    assert out["state"] == "expired"
    # A stale tab must not be able to open a checkout.
    assert out["order_id"] is None and out["key_id"] is None

    decision = decide(
        Proposal(grant_id, approved["cart_id"], WATCH_PAISE),
        policies=web.POLICIES,
        adapter=web.MARKETPLACE,
        ledger=web.LEDGER,
    )
    assert decision.verdict is Verdict.DENY
    assert "mandate.expired" in decision.reason_code


# --- the unit, without HTTP ---------------------------------------------------


def basket(*items: CartItem, address: str = HOME) -> Cart:
    return Cart("cart_x", "instamart", items, address)


def test_an_empty_basket_cannot_be_granted():
    with pytest.raises(GrantRefused):
        grant_for_cart(
            basket(), grant_id="grant_1", authorised_addresses=frozenset({HOME}), now=NOW
        )


def test_an_unclassifiable_line_does_not_clarify_under_a_grant(policies, ledger, monkeypatch):
    """A grant pins the basket, and the user approved these lines by reading them.

    Under a standing rule the same line is a CLARIFY, and should be: nobody has
    looked at it.
    """
    from bounded_mandate.merchant import MockMerchant

    cart = basket(CartItem("Whey protein 1kg", 32_000, ""))
    merchant = MockMerchant()
    merchant.hold(cart)

    grant = grant_for_cart(
        cart, grant_id="grant_1", authorised_addresses=frozenset({HOME}), now=NOW
    )
    decision = decide(
        Proposal("grant_1", cart.cart_id, cart.total_paise),
        policies={"grant_1": grant},
        adapter=merchant,
        ledger=ledger,
        now=NOW,
    )

    assert decision.verdict is Verdict.ALLOW
    assert "category.unknown" not in decision.reason_code


def test_the_same_line_still_clarifies_under_a_standing_rule(policy, ledger):
    from bounded_mandate.merchant import MockMerchant

    cart = basket(CartItem("Whey protein 1kg", 32_000, ""))
    merchant = MockMerchant()
    merchant.hold(cart)

    decision = decide(
        Proposal(policy.mandate_id, cart.cart_id, cart.total_paise),
        policies={policy.mandate_id: policy},
        adapter=merchant,
        ledger=ledger,
        now=NOW,
    )

    assert "category.unknown" in decision.reason_code


def test_a_basket_that_moved_after_approval_hands_out_no_checkout(client, monkeypatch):
    """Cart ids are content-addressed, so a changed basket answers to a different
    id. The order on Razorpay is then for a total nobody has read since."""
    grant_id = grant(client, refused(client)["cart_id"]).json()["grant"]["grant_id"]
    monkeypatch.setattr(web.MARKETPLACE, "fetch_cart", lambda _: None)

    out = client.get(f"/api/grant/{grant_id}").json()

    assert out["state"] == "stale"
    assert out["order_id"] is None and out["key_id"] is None


# --- what the checkout may fill in, and what it may never -----------------------


def test_a_charge_order_names_the_customer_so_a_card_can_be_remembered(client):
    """Not passing this is why the account had two captured payments and zero
    saved cards. With it, the card is entered once and every approval after is
    the saved card with no CVV — Standard Checkout runs the CVV-less flow by
    default on Visa, Mastercard and Amex."""
    grant(client, refused(client)["cart_id"])

    assert client.gateway.customers == ["cust_1"], "no customer attached to the charge"


def test_the_checkout_payload_carries_contact_details_and_nothing_more(client):
    """The card is Razorpay's to hold, not ours.

    Not a principle being defended — network tokenisation is exactly how every
    app saves a card, and this one does too. It is simply that the PAN lives
    behind the hosted form and a token comes back, so there is nothing
    card-shaped for this payload to carry and a regression that added one would
    be a real problem.
    """
    grant_id = grant(client, refused(client)["cart_id"]).json()["grant"]["grant_id"]

    payload = client.get(f"/api/grant/{grant_id}").json()

    assert set(payload["prefill"]) == {"name", "email", "contact"}
    body = json.dumps(payload).lower()
    for forbidden in ("card", "cvv", "pan", "expiry", "number"):
        assert forbidden not in body, f"the checkout payload mentions {forbidden}"
