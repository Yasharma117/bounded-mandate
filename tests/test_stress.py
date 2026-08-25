"""Attacks on the architecture, rather than descriptions of it.

Every test here is a thing someone could try. Several of them worked when first
written, and the docstrings say which — a suite that only records victories is
a suite nobody learned anything from.

Where an attack is *mitigated* rather than *eliminated*, the test says so and
pins the mitigation. Pretending defence-in-depth is prevention is how the layer
underneath quietly stops being maintained.
"""

from __future__ import annotations

import json

import pytest

from bounded_mandate import Cart, CartItem, Proposal, Verdict, decide
from bounded_mandate.categories import FEES, categorise, with_fees
from bounded_mandate.swiggy import SwiggyAdapter, SwiggyUnavailable, cart_id_for
from tests.conftest import HOME, NOW, merchant_holding

ADDRESS = {"address": HOME}


def bill(total, *lines):
    return {"lineItems": [{"label": lbl, "value": val} for lbl, val in lines], "toPay": total}


def cart_payload(items, total, *fees):
    return {
        "items": items,
        "billBreakdown": bill(
            total, ("Item total", str(sum(int(i["discountedFinalPrice"]) for i in items))), *fees
        ),
        "selectedAddressDetails": ADDRESS,
    }


def adapter(cart, search=None):
    return SwiggyAdapter(
        lambda tool, **_: (search or {}) if tool == "search_products" else cart,
        address_id="addr_1",
    )


def rule(policy, cart, ledger, policies, claimed=None):
    return decide(
        Proposal(policy.mandate_id, cart.cart_id, claimed or cart.total_paise),
        policies=policies,
        adapter=merchant_holding(cart),
        ledger=ledger,
        now=NOW,
    )


# --- attacks on the category check ------------------------------------------


class TestCategoryCannotBeSmuggled:
    def test_a_user_cannot_classify_goods_as_fees(self):
        """**This worked.** `fees` is allowed by every policy, so tagging a
        smartwatch with it cleared the scope check on anything. The adapter is
        now the only thing that may mint that category."""
        from bounded_mandate.swiggy import _category_for

        assert _category_for("Smartwatch", {"Smartwatch": FEES}) == ""

    def test_the_list_route_refuses_to_store_it(self, client):
        """Defence in depth: blocked at the boundary as well as at the reader."""
        response = client.put(
            "/api/list/usual",
            json={"item_names": ["Cow ghee 500ml"], "categories": {"Cow ghee 500ml": FEES}},
        )
        assert response.status_code == 400
        assert FEES in response.json()["detail"]

    def test_the_lookup_never_produces_it(self):
        for name in ("Delivery fee", "fees", "Platform fee", "Handling charge"):
            assert categorise(name) != FEES

    def test_a_list_category_does_not_leak_onto_a_different_product(self):
        """**This worked.** Matching was by substring, so a line "Milk" marked
        groceries also marked "Milk Chocolate Bar". Keyed on the resolved
        product now, exactly."""
        from bounded_mandate.swiggy import _category_for

        assigned = {"Amul Toned Milk 1 L": "groceries"}
        assert _category_for("Amul Toned Milk 1 L", assigned) == "groceries"
        assert _category_for("Milk Chocolate Bar", assigned) == categorise("Milk Chocolate Bar")

    def test_a_substituted_product_is_reported_not_hidden(self):
        """Asking for "Milk" and receiving "Milk Frother Machine Deluxe" is a
        different object wearing the word. No matching rule can decide whether a
        frother is milk — so the swap is surfaced for a person to judge, the
        same as an out-of-stock line.

        Mitigated, not eliminated: the category still rides along, and the cap
        is what stops an expensive substitution."""
        search = {
            "products": [
                {
                    "displayName": "Milk Frother Machine Deluxe Steel",
                    "variations": [
                        {
                            "skuId": "S9",
                            "quantityDescription": "",
                            "price": {"offerPrice": 499},
                            "isInStockAndAvailable": True,
                        }
                    ],
                }
            ]
        }
        cart = cart_payload(
            [
                {
                    "displayName": "Milk Frother Machine Deluxe Steel",
                    "discountedFinalPrice": "499",
                    "quantity": 1,
                }
            ],
            "499",
        )
        live = adapter(cart, search)
        live.create_cart(["Milk"], categories={"Milk": "groceries"})
        report = live.read_cart()
        assert report.substituted == (("Milk", "Milk Frother Machine Deluxe Steel"),)
        assert report.diverged, "a rename must reach the same surface as an out-of-stock line"

    def test_no_rule_pretends_to_judge_a_substitution(self):
        """**Known limitation, deliberately not papered over.**

        "Milk" resolving to "Amul Toned Milk 1 L" is the thing that was asked
        for. "Milk" resolving to "Milk Frother Machine Deluxe Steel" is a
        different object wearing the word. They have the same word count, the
        same overlap, and the substring lookup calls both groceries — no cheap
        signal separates them.

        A heuristic that got this wrong would manufacture confidence in exactly
        the case it failed on, so there is none. Every rename is reported, the
        card shows what resolved, and the cap bounds the cost. If an agent can
        steer resolution, that is the residual risk in this design.
        """
        from bounded_mandate.categories import categorise

        assert (
            categorise("Amul Toned Milk 1 L") == categorise("Milk Frother Machine") == "groceries"
        )


# --- attacks on the money path ----------------------------------------------


class TestMoneyCannotBeHidden:
    def test_a_charge_left_off_the_lines_is_refused(self):
        """A fee labelled to look like an item total gets skipped as a line but
        still counted in `toPay`. Reconciliation catches it — this is the check
        earning its place."""
        sneaky = {
            "items": [{"displayName": "Atta", "discountedFinalPrice": "100", "quantity": 1}],
            "billBreakdown": bill("1000", ("Item total", "100"), ("Subtotal", "900")),
            "selectedAddressDetails": ADDRESS,
        }
        with pytest.raises(SwiggyUnavailable, match="does not add up"):
            adapter(sneaky).read_cart()

    def test_an_item_priced_below_zero_is_refused(self):
        """**This worked.** A negative line exists only to pull a total under a
        cap. A discount belongs on the bill, not on a line of goods."""
        offset = {
            "items": [
                {"displayName": "Smartwatch", "discountedFinalPrice": "15000", "quantity": 1},
                {
                    "displayName": "Loyalty adjustment",
                    "discountedFinalPrice": "-14500",
                    "quantity": 1,
                },
            ],
            "billBreakdown": bill("500", ("Item total", "500")),
            "selectedAddressDetails": ADDRESS,
        }
        with pytest.raises(SwiggyUnavailable, match="below zero"):
            adapter(offset).read_cart()

    def test_an_empty_cart_authorises_nothing(self):
        """**This worked.** An empty basket passed every check, wrote an ALLOW
        to the ledger and burned a frequency slot on nothing."""
        empty = {"items": [], "billBreakdown": bill("0"), "selectedAddressDetails": ADDRESS}
        with pytest.raises(SwiggyUnavailable, match="nothing to authorise"):
            adapter(empty).read_cart()

    def test_fees_still_hit_the_cap(self, policy, policies, ledger):
        """Fees clear the *scope* check by construction, so the cap has to be
        what stops an absurd one. If both let it through, `fees` would be a
        hole rather than a convenience."""
        payload = cart_payload(
            [{"displayName": "Atta", "discountedFinalPrice": "100", "quantity": 1}],
            "50100",
            ("Delivery fee", "50000"),
        )
        cart = adapter(payload).read_cart().cart
        decision = rule(policy, cart, ledger, policies)
        assert "cap.exceeded" in decision.reason_code
        assert "category.not_allowed" not in decision.reason_code


# --- attacks on provenance --------------------------------------------------


class TestProvenanceHolds:
    def test_the_agent_cannot_name_a_cart_that_was_never_built(self, policy, policies, ledger):
        """The id is a hash of contents. Inventing one names nothing."""
        decision = decide(
            Proposal(policy.mandate_id, "swiggy_deadbeefdeadbeef", 100),
            policies=policies,
            adapter=merchant_holding(),
            ledger=ledger,
            now=NOW,
        )
        assert decision.verdict is Verdict.DENY
        assert "provenance.cart_not_found" in decision.reason_code

    def test_a_cart_edited_after_proposing_no_longer_matches(self):
        """`get_cart` is session-scoped with no etag, so this is the whole
        reason the id is content-addressed."""
        before = cart_payload(
            [{"displayName": "Atta", "discountedFinalPrice": "100", "quantity": 1}], "100"
        )
        proposed = adapter(before).read_cart().cart

        after = json.loads(json.dumps(before))
        after["items"].append(
            {"displayName": "Smartwatch", "discountedFinalPrice": "15000", "quantity": 1}
        )
        after["billBreakdown"] = bill("15100", ("Item total", "15100"))
        assert adapter(after).fetch_cart(proposed.cart_id) is None

    def test_reordering_the_same_basket_is_the_same_cart(self):
        """Line order is the merchant's business. If it changed the id, every
        refetch would be a coin flip."""
        items = [CartItem("Milk", 7_000, "groceries"), CartItem("Eggs", 9_000, "groceries")]
        assert cart_id_for(items, 16_000) == cart_id_for(list(reversed(items)), 16_000)

    def test_a_lie_about_the_total_is_caught_whatever_the_cart(self, policy, policies, ledger):
        cart = Cart("swiggy_x", "instamart", (CartItem("Atta", 185_000, "groceries"),), HOME)
        decision = rule(policy, cart, ledger, policies, claimed=100)
        assert decision.verdict is Verdict.DENY
        assert "provenance.total_mismatch" in decision.reason_code


# --- attacks on authority ---------------------------------------------------


class TestAuthorityCannotBeWidened:
    def test_no_agent_tool_can_write_the_list(self):
        from bounded_mandate.agent import TOOLS

        names = {t["function"]["name"] for t in TOOLS}
        assert not [n for n in names if n != "read_shopping_list" and "list" in n]

    def test_no_agent_tool_asserts_a_category(self):
        from bounded_mandate.agent import TOOLS

        for tool in TOOLS:
            params = tool["function"].get("parameters", {}).get("properties", {})
            assert not [k for k in params if "categor" in k.lower()]

    def test_the_proposal_vocabulary_admits_nothing_else(self):
        """An injected agent inventing a raised cap has nowhere to put it."""
        assert set(Proposal.__dataclass_fields__) == {
            "mandate_id",
            "cart_id",
            "claimed_total_paise",
        }

    def test_a_policy_cannot_be_widened_by_anything_a_proposal_carries(
        self, policy, policies, ledger
    ):
        cart = Cart("c1", "blinkit", (CartItem("Atta", 100, "groceries"),), HOME)
        decision = rule(policy, cart, ledger, policies)
        assert "merchant.not_allowed" in decision.reason_code

    def test_fees_are_the_only_category_added_by_construction(self):
        """`with_fees` is a deliberate widening of every policy. If it ever
        added a second thing, that would be a scope grant nobody reviewed."""
        assert with_fees({"groceries"}) - {"groceries"} == {FEES}
        assert with_fees(set()) == {FEES}


# --- attacks on the firewall ------------------------------------------------


class TestNothingReachesCheckout:
    def test_no_commerce_backend_exposes_a_way_to_order(self):
        from bounded_mandate.merchant import Marketplace

        for backend in (Marketplace(), SwiggyAdapter(lambda *a, **k: {}, address_id="a")):
            for wire in ("checkout", "confirm_order", "place_order", "order"):
                assert not hasattr(backend, wire), f"{type(backend).__name__}.{wire}"

    def test_the_allowlist_gates_every_call(self):
        seen: list[str] = []
        live = SwiggyAdapter(lambda t, **k: seen.append(t) or {}, address_id="a")
        for wire in ("checkout", "confirm_order", "place_order"):
            with pytest.raises(SwiggyUnavailable):
                live._invoke(wire)
        assert seen == []


# --- attacks that need history ----------------------------------------------


class TestHistoryCannotBeOutrun:
    def cart(self, name, paise=1_000):
        return Cart(name, "instamart", (CartItem("Atta", paise, "groceries"),), HOME)

    def test_a_burst_of_refusals_gates_the_next_clean_proposal(self, policy, policies, ledger):
        """An agent that has spent the last minutes testing the fence does not
        get waved through the moment it finally proposes something legal."""
        for i in range(4):
            decide(
                Proposal(policy.mandate_id, f"c{i}", 999_999),
                policies=policies,
                adapter=merchant_holding(self.cart(f"c{i}")),
                ledger=ledger,
                now=NOW,
            )
        clean = decide(
            Proposal(policy.mandate_id, "c9", 1_000),
            policies=policies,
            adapter=merchant_holding(self.cart("c9")),
            ledger=ledger,
            now=NOW,
        )
        assert clean.verdict is Verdict.ESCALATE
        assert "agent.probing" in clean.reason_code

    def test_the_gate_lifts_once_the_burst_is_old(self, policy, policies, ledger):
        """It escalates rather than denies for exactly this reason: the
        proposal in front of you may be fine, and a permanent lock-out from a
        bad minute would be its own denial of service."""
        from datetime import timedelta

        for i in range(4):
            decide(
                Proposal(policy.mandate_id, f"c{i}", 999_999),
                policies=policies,
                adapter=merchant_holding(self.cart(f"c{i}")),
                ledger=ledger,
                now=NOW,
            )
        later = decide(
            Proposal(policy.mandate_id, "c10", 1_000),
            policies=policies,
            adapter=merchant_holding(self.cart("c10")),
            ledger=ledger,
            now=NOW + timedelta(hours=2),
        )
        assert later.verdict is Verdict.ALLOW

    def test_rewriting_a_refusal_into_an_approval_breaks_the_chain(
        self, policy, policies, ledger, tmp_path
    ):
        """The ledger is the only record of what was decided. If it could be
        edited afterwards, every other guarantee here would be a story."""
        from bounded_mandate import Ledger
        from bounded_mandate.ledger import ChainBroken

        decide(
            Proposal(policy.mandate_id, "c1", 999),
            policies=policies,
            adapter=merchant_holding(self.cart("c1")),
            ledger=ledger,
            now=NOW,
        )
        path = ledger.path
        path.write_text(path.read_text().replace('"DENY"', '"ALLOW"'))
        with pytest.raises(ChainBroken):
            Ledger(path).verify()

    def test_the_same_basket_twice_in_a_window_is_one_charge(self, policy, policies, ledger):
        """Content-addressing means a retried basket really is the same id, so
        duplicate suppression survives the move off merchant-issued ids."""
        items = [CartItem("Atta", 1_000, "groceries")]
        same = cart_id_for(items, 1_000)
        cart = Cart(same, "instamart", tuple(items), HOME)

        first = decide(
            Proposal(policy.mandate_id, same, 1_000),
            policies=policies,
            adapter=merchant_holding(cart),
            ledger=ledger,
            now=NOW,
        )
        second = decide(
            Proposal(policy.mandate_id, same, 1_000),
            policies=policies,
            adapter=merchant_holding(cart),
            ledger=ledger,
            now=NOW,
        )
        assert first.verdict is Verdict.ALLOW
        assert second.verdict is Verdict.DENY
        assert "duplicate.suppressed" in second.reason_code
