from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bounded_mandate import Cart, CartItem, Ledger, MandateStatus, Policy
from bounded_mandate.merchant import MockMerchant

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
        categories=frozenset({"groceries"}),
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
