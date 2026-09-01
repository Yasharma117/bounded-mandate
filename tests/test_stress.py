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

from bounded_mandate import Cart, CartItem, Proposal, Verdict, decide, web
from bounded_mandate.basket import USUAL_GROCERIES
from bounded_mandate.categories import FEES, categorise, with_fees
from bounded_mandate.merchant import Marketplace
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

        # The category the table itself gives it, never the one the caller asked
        # for — which is the property. That it is now placeable at all is the
        # devices table doing its job.
        assert _category_for("Smartwatch", {"Smartwatch": FEES}) == "electronics"
        assert _category_for("Zorblex 9000", {"Zorblex 9000": FEES}) == ""

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
                            "spinId": "SPIN-S9",
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

    def test_an_appliance_wearing_a_grocery_word_is_not_a_grocery(self):
        """This used to pass the other way, and was written up as a limitation:
        "Milk" resolving to "Milk Frother Machine Deluxe Steel" was called
        groceries, because a substring table asked only whether `milk` appeared.

        It was not a limitation, it was an ordering mistake. `frother` is a
        cheap signal and it does separate them — it just has to be *looked at
        first*. Found live: `Apple iPhone 17 Pro` came back as groceries on a
        ₹1,29,900 line and was stopped only by the cap, which is the wrong guard
        doing the work. A ₹900 Apple Watch band clears the cap.
        """
        from bounded_mandate.categories import categorise

        assert categorise("Amul Toned Milk 1 L") == "groceries"
        for wearing in (
            "Milk Frother Machine Deluxe Steel",
            "Apple iPhone 17 Pro | 256 GB Storage",
            "Apple Watch Series 10",
            "Rice cooker 1.8L",
            "Egg boiler electric",
            "Philips Air Fryer NA120/00",
        ):
            assert categorise(wearing) == "electronics", wearing

    def test_no_rule_pretends_to_judge_a_substitution(self):
        """**Known limitation, deliberately not papered over — narrowed, not closed.**

        The appliance case above is handled. The one that remains is a rename
        *within* a category: "Milk" resolving to a ₹900 imported cheese is still
        groceries, and no cheap signal says the user did not mean it.

        A heuristic that guessed here would manufacture confidence in exactly the
        case it failed on, so there is none. Every rename is reported, the card
        shows what resolved, and the cap bounds the cost. If an agent can steer
        resolution inside a category, that is the residual risk in this design.
        """
        from bounded_mandate.categories import categorise

        assert categorise("Amul Toned Milk 1 L") == categorise("Milk Powder 1kg") == "groceries"

    def test_a_name_the_table_cannot_place_still_fails_closed(self):
        """The direction that matters. A new table cannot have made anything
        confident that was previously blank."""
        from bounded_mandate.categories import categorise

        assert categorise("Zorblex 9000") == ""


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
        """The property is that nothing the agent calls *writes* a list — not
        that no tool has the word in its name.

        `propose_list` writes one out for the account holder to approve, which
        is the same shape as everything else here: it proposes, somebody else
        decides. Confirming is an ordinary `POST /api/lists` that no tool
        reaches. So the guard is behavioural now, and the two below check it by
        running the thing rather than reading its name.
        """
        from bounded_mandate.agent import TOOLS

        names = {t["function"]["name"] for t in TOOLS}
        assert names == {
            "read_shopping_list",
            "propose_list",
            "search_catalog",
            "create_cart",
            "request_charge",
        }

    def test_drafting_a_list_does_not_create_one(self, client):
        before = {k: v.item_names for k, v in web.LISTS.items()}
        from bounded_mandate.agent import AgentRun, BuyerAgent

        agent = BuyerAgent(
            marketplace=Marketplace(),
            policies={},
            ledger=web.LEDGER,
            mandate_id="mdt_1",
            delivery_address=HOME,
            client=object(),
        )
        run = AgentRun("x")
        out = agent._dispatch(
            run, "propose_list", {"name": "Snacks", "item_names": ["Blue Lays x3"]}
        )

        assert out["drafted"] is True
        assert run.draft.name == "Snacks"
        # Nothing stored, scheduled, or orderable against.
        assert {k: v.item_names for k, v in web.LISTS.items()} == before
        assert "Snacks" not in {v.name for v in web.LISTS.values()}

    def test_only_the_route_the_agent_cannot_reach_creates_one(self, client):
        """Approving a draft is an ordinary user action, and the reason it is
        safe is that no tool can perform it."""
        import inspect

        from bounded_mandate.agent import BuyerAgent

        source = inspect.getsource(BuyerAgent)
        for forbidden in ("api/lists", "LISTS[", "with_item", "without("):
            assert forbidden not in source, f"the agent reaches {forbidden}"

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


# --- attacks that are just two of something at once --------------------------


class TestConcurrencyCannotUndoTheGuarantees:
    """Both of these were found by an outside review and reproduced before they
    were fixed. Neither needs a hostile agent — only two things happening at
    once, which this app does on its own: every route that writes is a sync
    `def` and so runs in anyio's threadpool, and the scheduler adds
    `asyncio.to_thread(run_due_lists)` on every tick.
    """

    def test_the_chain_survives_concurrent_appends(self):
        """**This broke.** `append` was read-head → compute → write with no
        lock, so two threads inside it both read the same head and both minted
        the same `seq`. 8 threads x 25 appends produced
        `CHAIN BROKEN: entry 1: out-of-order seq 0`.

        Which made it worse than an ordinary race: `/api/home` renders
        `chain_intact`, so the failure mode was the tamper-evidence screen
        accusing itself, in front of whoever was being shown the tamper
        evidence.
        """
        import pathlib
        import tempfile
        import threading

        from bounded_mandate.ledger import Ledger

        ledger = Ledger(pathlib.Path(tempfile.mkdtemp()) / "ledger.jsonl")
        start = threading.Barrier(8)

        def hammer(who: int) -> None:
            start.wait()  # go together, so the window is as wide as it gets
            for i in range(25):
                ledger.append({"who": who, "i": i})

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert ledger.verify() == 200, "an append was lost or the chain reordered"

    def test_one_cart_cannot_be_authorised_twice_at_once(self):
        """**This broke, and it is the one that moves money.** The idempotency
        key was always right; the check was not atomic with the write. Two
        identical proposals arriving together both read `charged_keys` before
        either recorded, so both saw the key absent and both returned ALLOW —
        and `_settle` then created two Razorpay orders for one basket.

        Measured at six concurrent copies: six ALLOWs. Adding a lock to `append`
        alone did *not* fix it — serialising the write does nothing when the
        read that decides is outside. The check and the write have to be one
        critical section.
        """
        import pathlib
        import tempfile
        import threading
        from datetime import UTC, datetime

        from bounded_mandate.ledger import Ledger

        shop = Marketplace()
        cart = shop.create_cart(list(USUAL_GROCERIES), merchant="instamart")
        ledger = Ledger(pathlib.Path(tempfile.mkdtemp()) / "ledger.jsonl")
        proposal = Proposal("mdt_demo", cart.cart_id, cart.total_paise)
        policies = {"mdt_demo": web.POLICIES["mdt_demo"]}

        out: list = []
        start = threading.Barrier(6)

        def race() -> None:
            start.wait()
            out.append(
                decide(
                    proposal,
                    policies=policies,
                    adapter=shop,
                    ledger=ledger,
                    now=datetime.now(UTC),
                )
            )

        threads = [threading.Thread(target=race) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = [d for d in out if d.verdict is Verdict.ALLOW]
        assert len(allowed) == 1, f"{len(allowed)} authorisations of one cart"
        assert all("duplicate.suppressed" in d.reason_code for d in out if d not in allowed)
        ledger.verify()


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


class TestAnInterruptCannotBeSilenced:
    """Found by probing the home screen, and introduced by it.

    Idempotency keys are `sha256(mandate | window | cart)[:32]` — deterministic,
    and cart ids are predictable on both backends (sequential on the mock, a
    content hash on Swiggy). So the key of a decision that has not been made yet
    is computable by anyone who can see one.

    The engine still refused the basket, so no money was ever at risk. What was
    at risk is the only channel by which the user finds out — and silencing the
    interrupt defeats an escalation as thoroughly as widening the cap would,
    while looking like nothing happened.
    """

    def test_a_decision_cannot_be_dismissed_before_it_is_made(self, client):
        from datetime import UTC, datetime

        from bounded_mandate.engine import idempotency_key

        # Observe one cart id, predict the next.
        seen = web.MARKETPLACE.create_cart(["Brown bread"], merchant="instamart")
        number = int(seen.cart_id.rsplit("_", 1)[1])
        ahead = idempotency_key(
            web.POLICIES["mdt_demo"], f"instamart_cart_{number + 1}", datetime.now(UTC)
        )

        refused = client.post("/api/home/seen", json={"idempotency_key": ahead})
        assert refused.status_code == 404

        client.post(
            "/api/proposal",
            json={
                "items": [*USUAL_GROCERIES, "Bluetooth earbuds", "Phone case"],
                "claimed_total_paise": 240_000,
            },
        )
        assert client.get("/api/home").json()["state"] == "needs_you"

    def test_a_decision_that_happened_can_still_be_dismissed(self, client):
        """The guard must not have closed on the thing the button is for."""
        client.post(
            "/api/proposal",
            json={
                "items": [*USUAL_GROCERIES, "Bluetooth earbuds", "Phone case"],
                "claimed_total_paise": 240_000,
            },
        )
        key = client.get("/api/home").json()["decision"]["idempotency_key"]

        assert client.post("/api/home/seen", json={"idempotency_key": key}).status_code == 200
        assert client.get("/api/home").json()["state"] != "needs_you"


class TestTheAgentCannotGoShopping:
    """Left to itself the agent built the right cart, abandoned it, and rebuilt
    at whichever shop looked cheaper — then charged that one.

    The engine refused it, so the money was never at risk; the user's
    understanding was, because the refusal named a shop they never asked for.
    Both causes were ours rather than the model's: `merchant` was a **required**
    tool parameter, so the model had to name a shop while knowing nothing about
    which ones are allowed, and nothing stopped it changing its mind afterwards.
    """

    def test_naming_a_shop_is_optional_so_it_is_never_forced_to_guess(self):
        """Two fields, opposite treatment, same principle — a schema gets a more
        honest answer than a prompt does, so ask only what can be answered.

        `merchant` is optional because the agent cannot see which shops are
        allowed: required, it guessed, and the user got a refusal naming a shop
        they never mentioned. `asked_for` is required *because* it carries a
        `not_said` value — the model can always answer it truthfully, and being
        asked at the moment of acting is the point.
        """
        from bounded_mandate.agent import TOOLS

        schema = next(t for t in TOOLS if t["function"]["name"] == "create_cart")
        required = schema["function"]["parameters"]["required"]

        assert "merchant" not in required, "a required shop makes it guess"
        assert "asked_for" in required
        assert "not_said" in schema["function"]["parameters"]["properties"]["asked_for"]["enum"]

    def test_omitting_the_shop_uses_the_account_s_usual_one(self, ledger):
        from bounded_mandate.agent import BuyerAgent

        agent = BuyerAgent(
            marketplace=Marketplace(),
            policies={},
            ledger=ledger,
            mandate_id="mdt_1",
            delivery_address=HOME,
            client=object(),
        )
        assert (
            agent._dispatch(
                None, "create_cart", {"item_names": ["Brown bread"], "asked_for": "once"}
            )["merchant"]
            == "instamart"
        )

    def test_the_first_cart_fixes_the_shop_for_the_rest_of_the_run(self, ledger):
        """Rebuilding a basket is legitimate — an item may not be stocked.
        Moving shops halfway through is not."""
        from bounded_mandate.agent import BuyerAgent

        agent = BuyerAgent(
            marketplace=Marketplace(),
            policies={},
            ledger=ledger,
            mandate_id="mdt_1",
            delivery_address=HOME,
            client=object(),
        )
        first = agent._dispatch(
            None, "create_cart", {"item_names": ["Brown bread"], "asked_for": "once"}
        )
        assert first["merchant"] == "instamart"

        moved = agent._dispatch(
            None,
            "create_cart",
            {"item_names": ["Brown bread"], "merchant": "blinkit", "asked_for": "once"},
        )
        assert "error" in moved
        assert "instamart" in moved["error"]

        # Same shop again is fine: that is a corrected basket, not a new shop.
        assert "cart_id" in agent._dispatch(
            None,
            "create_cart",
            {"item_names": ["Curd 400g"], "merchant": "instamart", "asked_for": "once"},
        )
