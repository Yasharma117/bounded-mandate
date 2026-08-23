"""The mock merchant, and the demo it makes deterministic."""

from __future__ import annotations

import pytest

from bounded_mandate import Ledger, Proposal, Verdict, decide
from bounded_mandate.merchant import CATALOG, USUAL_GROCERIES, MockMerchant, UnknownItem
from tests.conftest import HOME, NOW

ESCALATION_EXTRAS = ["Bluetooth earbuds", "Phone case"]


def test_the_usual_basket_is_the_demos_1850():
    cart = MockMerchant().create_cart(list(USUAL_GROCERIES), delivery_address=HOME)
    assert (len(cart.items), cart.total_paise) == (12, 185_000)


def test_adding_the_off_scope_items_makes_it_the_demos_2400():
    names = [*USUAL_GROCERIES, *ESCALATION_EXTRAS]
    cart = MockMerchant().create_cart(names, delivery_address=HOME)
    assert (len(cart.items), cart.total_paise) == (14, 240_000)


def test_fetch_cart_returns_exactly_what_was_built():
    merchant = MockMerchant()
    built = merchant.create_cart(list(USUAL_GROCERIES), delivery_address=HOME)
    assert merchant.fetch_cart(built.cart_id) == built


def test_unknown_cart_id_is_none_not_an_invention():
    assert MockMerchant().fetch_cart("cart_999") is None


def test_unstocked_item_is_refused():
    with pytest.raises(UnknownItem):
        MockMerchant().create_cart(["Ferrari"], delivery_address=HOME)


def test_search_finds_by_name_and_by_category():
    merchant = MockMerchant()
    assert [i.name for i in merchant.search("ghee")] == ["Cow ghee 500ml"]
    assert len(merchant.search("groceries")) == 12


def test_the_catalog_stocks_something_it_cannot_classify():
    """So the CLARIFY path has a real cause, not a synthetic one."""
    assert CATALOG["Whey protein 1kg"].category == ""


# --- the hero, end to end ----------------------------------------------------


def test_lying_agent_against_the_real_merchant(policies, ledger: Ledger):
    """Scene 3. The agent builds a cart with a ₹15,000 item hidden in it and
    reports only the grocery subtotal. The engine never sees the claim as fact.
    """
    merchant = MockMerchant()
    real = merchant.create_cart([*USUAL_GROCERIES, "Smartwatch"], delivery_address=HOME)

    # What the agent says. Note it is a *true* number for the groceries alone.
    lie = Proposal(mandate_id="mdt_1", cart_id=real.cart_id, claimed_total_paise=185_000)

    decision = decide(lie, policies=policies, adapter=merchant, ledger=ledger, now=NOW)

    assert decision.verdict is Verdict.DENY
    assert "provenance.total_mismatch" in decision.reason_code
    assert decision.total_paise == 1_685_000  # what the merchant really holds
    assert ledger.verify() == 1


def test_honest_agent_on_the_same_merchant_is_allowed(policies, ledger: Ledger):
    """The control. Same merchant, same engine — the truth passes."""
    merchant = MockMerchant()
    cart = merchant.create_cart(list(USUAL_GROCERIES), delivery_address=HOME)
    proposal = Proposal("mdt_1", cart.cart_id, cart.total_paise)

    decision = decide(proposal, policies=policies, adapter=merchant, ledger=ledger, now=NOW)

    assert decision.verdict is Verdict.ALLOW
    assert decision.reason_code == "ok.in_policy"
