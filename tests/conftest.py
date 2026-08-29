from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bounded_mandate import Cart, CartItem, Ledger, MandateStatus, Policy, web
from bounded_mandate.basket import seed_lists
from bounded_mandate.categories import with_fees
from bounded_mandate.merchant import Marketplace, MockMerchant
from bounded_mandate.razorpay_gateway import GatewayError

NOW = datetime(2026, 8, 24, 9, 41, tzinfo=UTC)
HOME = "12 Nandidurga Rd, Bengaluru"


def merchant_holding(*carts: Cart) -> MockMerchant:
    """A merchant staged with exact carts, for engine tests that need edge cases."""
    merchant = MockMerchant()
    for cart in carts:
        merchant.hold(cart)
    return merchant


def groceries(*, cart_id: str = "cart_1", total_paise: int = 185_000) -> Cart:
    return Cart(
        cart_id=cart_id,
        merchant="instamart",
        items=(CartItem("12 grocery items", total_paise, "groceries"),),
        delivery_address=HOME,
    )


@pytest.fixture
def policy() -> Policy:
    return Policy(
        mandate_id="mdt_1",
        per_txn_max_paise=200_000,  # ₹2,000
        merchants=frozenset({"instamart"}),
        categories=with_fees({"groceries"}),
        delivery_addresses=frozenset({HOME}),
        max_charges_per_window=2,
        window_days=7,
        status=MandateStatus.ACTIVE,
    )


@pytest.fixture
def policies(policy: Policy) -> dict[str, Policy]:
    return {policy.mandate_id: policy}


@pytest.fixture
def ledger(tmp_path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


# --- the app under test ------------------------------------------------------
#
# Shared rather than owned by `test_web`, because the stress suite attacks the
# same HTTP surface and two copies of a fixture drift.


class FakeGateway:
    def __init__(self, *, order_error=None, verify_error=None):
        self.order_error, self.verify_error = order_error, verify_error
        self.charged = []
        #: Which customer each charge order was attached to. `None` means the
        #: card cannot be remembered, which is a real state worth seeing.
        self.customers = []

    def create_customer(self, **_):
        return "cust_1"

    def create_mandate_order(self, customer_id, *, max_amount_paise, **_):
        if self.order_error:
            raise self.order_error
        if not 100 <= max_amount_paise <= 10_000_000:
            raise GatewayError("mandate cap must be between 100 and 10000000 paise")
        return SimpleNamespace(
            order_id="order_1", customer_id=customer_id, amount_paise=100, key_id="rzp_test_key"
        )

    def verify_registration(self, *_):
        if self.verify_error:
            raise self.verify_error

    def token_for(self, _):
        return "token_abc"

    key_id = "rzp_test_key"

    def create_charge_order(self, *, amount_paise, idempotency_key, description, customer_id=None):
        self.charged.append(amount_paise)
        self.customers.append(customer_id)
        return "order_charge_1"


#: Captured at import, before any test can add a one-time grant to the live
#: dict. A grant is an ordinary Policy in `web.POLICIES`, so without this the
#: mandates one test mints are still standing in the next.
SEED_POLICIES = dict(web.POLICIES)
SEED_DELIVERY = web.DELIVERY_ID


@pytest.fixture
def client(monkeypatch, tmp_path):
    gw = FakeGateway()
    monkeypatch.setattr(web, "RazorpayGateway", lambda *a, **k: gw)
    monkeypatch.setattr(web, "LEDGER", Ledger(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(web, "MARKETPLACE", Marketplace())
    monkeypatch.setattr(web, "LISTS", seed_lists())
    monkeypatch.setattr(web, "POLICIES", dict(SEED_POLICIES))
    monkeypatch.setattr(web, "GRANTS", {})
    # Where things get delivered is module state too, and choosing an address
    # rewrites the policy — so a test that moves it would otherwise move it for
    # every test after.
    monkeypatch.setattr(web, "DELIVERY_ID", SEED_DELIVERY)
    c = TestClient(web.app)
    c.gateway = gw
    return c
