"""Real Instamart, behind the same one-method seam.

The first class here is the one that matters most. Instamart checkout is
COD-only, real, and non-cancellable — there is no test mode on Swiggy's side —
so the property under test is that no code path reaches it. That test is
load-bearing: it is what stops a future edit from helpfully wiring checkout.
"""

from __future__ import annotations

import inspect
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from bounded_mandate import Proposal, Verdict, decide
from bounded_mandate.categories import categorise, with_fees
from bounded_mandate.engine import Cart, CartItem, Policy
from bounded_mandate.ledger import Ledger
from bounded_mandate.swiggy import (
    ALLOWED_TOOLS,
    THUMB,
    SwiggyAdapter,
    SwiggyUnavailable,
    _thumb,
    cart_id_for,
    to_paise,
)
from tests.conftest import NOW

PAYLOADS = Path(__file__).parent / "payloads"


def _grocery_policy():
    from tests.conftest import HOME

    return Policy(
        mandate_id="mdt_1",
        per_txn_max_paise=200_000,
        merchants=frozenset({"instamart"}),
        categories=with_fees({"groceries"}),
        delivery_addresses=frozenset({HOME}),
        max_charges_per_window=2,
        window_days=7,
    )


def _holding(cart):
    from tests.conftest import merchant_holding

    return merchant_holding(cart)


#: Tools that place a real, non-cancellable, cash-on-delivery order.
LIVE_WIRES = ("checkout", "confirm_order", "place_order", "place_food_order")


# --- the firewall -----------------------------------------------------------


class TestNoPathToCheckout:
    def test_the_adapter_exposes_no_way_to_order(self):
        """Not guarded — absent. A guard can be argued past; a missing method
        cannot be called."""
        for wire in LIVE_WIRES:
            assert not hasattr(SwiggyAdapter, wire), f"SwiggyAdapter.{wire} exists"

    def test_the_module_never_names_a_checkout_tool(self):
        """`_invoke` refuses anything outside ALLOWED_TOOLS, but a tool name
        appearing in the source at all is the smell worth failing on — it means
        someone started."""
        from bounded_mandate import swiggy

        source = inspect.getsource(swiggy)
        # The docstring explains *why* these are absent, so only look at code.
        code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
        body = code.split('"""', 2)[-1]
        for wire in LIVE_WIRES:
            assert f'"{wire}"' not in body, f"{wire} is named in swiggy.py"

    def test_the_allowlist_is_read_and_cart_only(self):
        assert set(ALLOWED_TOOLS) == {
            "get_addresses",
            "search_products",
            "update_cart",
            "get_cart",
            "clear_cart",
        }
        assert not set(ALLOWED_TOOLS) & set(LIVE_WIRES)

    def test_invoking_an_unlisted_tool_is_refused_before_it_is_called(self):
        called: list[str] = []

        def spy(tool, **_):
            called.append(tool)
            return {}

        adapter = SwiggyAdapter(spy, address_id="addr_1")
        with pytest.raises(SwiggyUnavailable, match="not a tool this adapter may call"):
            adapter._invoke("checkout", addressId="addr_1")
        assert called == [], "the refusal must happen before the call, not after"


# --- money ------------------------------------------------------------------


class TestMoney:
    def test_both_shapes_the_two_endpoints_return(self):
        """`search_products` gives numbers, `get_cart` gives strings. Same
        answer either way."""
        assert to_paise(275) == 27_500
        assert to_paise("275") == 27_500
        assert to_paise(275.5) == 27_550
        assert to_paise("275.50") == 27_550
        assert to_paise("₹1,850.00") == 185_000

    def test_sub_paise_precision_is_refused_rather_than_rounded(self):
        """Rounding is a decision this module has no right to make quietly."""
        with pytest.raises(SwiggyUnavailable, match="sub-paise"):
            to_paise("10.005")

    def test_nonsense_is_refused(self):
        for bad in ("gratis", "nine", None, True, {}):
            with pytest.raises(SwiggyUnavailable):
                to_paise(bad)

    def test_free_is_a_real_bill_value_meaning_zero(self):
        """Observed on a live cart: "Delivery Partner Fee" reads `FREE`, not
        `0`. Refusing it would refuse every cart with free delivery."""
        assert to_paise("FREE") == 0
        assert to_paise("free") == 0
        assert to_paise("") == 0


# --- the content-addressed id ----------------------------------------------


class TestCartIdentity:
    def items(self, *pairs):
        return [CartItem(name, paise, "groceries") for name, paise in pairs]

    def test_the_same_basket_hashes_the_same_whatever_order_it_arrives_in(self):
        """Line order is the merchant's business, not a change to the basket."""
        a = cart_id_for(self.items(("Milk", 7_000), ("Eggs", 9_000)), 16_000)
        b = cart_id_for(self.items(("Eggs", 9_000), ("Milk", 7_000)), 16_000)
        assert a == b

    def test_a_changed_basket_is_a_different_id(self):
        base = self.items(("Milk", 7_000))
        assert cart_id_for(base, 7_000) != cart_id_for(self.items(("Milk", 7_500)), 7_500)
        assert cart_id_for(base, 7_000) != cart_id_for(
            self.items(("Milk", 7_000), ("Eggs", 9_000)), 16_000
        )

    def test_a_changed_total_alone_is_a_different_id(self):
        """Fees and taxes move without any line moving. That is still a
        different thing to authorise."""
        base = self.items(("Milk", 7_000))
        assert cart_id_for(base, 7_000) != cart_id_for(base, 7_500)


# --- category, failing closed -----------------------------------------------


class TestCategory:
    def test_the_staples_are_placed(self):
        assert categorise("Aashirvaad Atta 5kg") == "groceries"
        assert categorise("Amul Toned Milk 1L") == "groceries"
        assert categorise("Fresh Onion 1kg") == "groceries"
        assert categorise("Britannia Brown Bread") == "groceries"

    def test_what_it_cannot_place_stays_blank(self):
        """Blank is the answer that makes the engine ask instead of assume."""
        assert categorise("Sony WH-1000XM5 Headphones") == ""
        assert categorise("Bluetooth Earbuds") == ""
        assert categorise("") == ""

    def test_an_unplaceable_item_makes_the_engine_ask(self, policy, policies, ledger):
        """The whole reason it fails closed: an item nobody could classify must
        reach CLARIFY, never slip through as groceries."""
        from bounded_mandate.engine import Cart
        from tests.conftest import HOME, merchant_holding

        cart = Cart(
            cart_id="swiggy_x",
            merchant="instamart",
            items=(CartItem("Sony Headphones", 40_000, categorise("Sony Headphones")),),
            delivery_address=HOME,
        )
        decision = decide(
            Proposal(policy.mandate_id, cart.cart_id, 40_000),
            policies=policies,
            adapter=merchant_holding(cart),
            ledger=ledger,
            now=NOW,
        )
        assert decision.verdict is Verdict.CLARIFY
        assert "category.unknown" in decision.reason_code


# --- the adapter, against captured payloads ---------------------------------


def payload(name: str) -> dict:
    path = PAYLOADS / f"{name}.json"
    if not path.exists():
        pytest.skip(f"no captured payload: {path.name} — run the S0 capture")
    return json.loads(path.read_text())


class TestAdapter:
    def adapter(self, responses: dict) -> SwiggyAdapter:
        return SwiggyAdapter(lambda tool, **_: responses.get(tool, {}), address_id="addr_1")

    def test_search_flattens_to_one_row_per_sku(self):
        """`update_cart` takes skuIds, so a product with three pack sizes is
        three different things you can buy."""
        adapter = self.adapter({"search_products": payload("swiggy_search")})
        offers = adapter.search("milk")
        assert offers, "the captured payload has no products"
        assert len({o.sku_id for o in offers}) == len(offers), "skuIds must be unique"
        assert all(o.price_paise > 0 for o in offers)

    def test_the_cart_reads_back_with_a_content_addressed_id(self):
        adapter = self.adapter({"get_cart": payload("swiggy_cart")})
        report = adapter.read_cart()
        assert report.cart.cart_id.startswith("swiggy_")
        assert report.cart.merchant == "instamart"
        assert report.total_paise > 0

    def test_fetch_cart_returns_the_cart_only_when_the_id_still_matches(self):
        adapter = self.adapter({"get_cart": payload("swiggy_cart")})
        real = adapter.read_cart().cart
        assert adapter.fetch_cart(real.cart_id) is not None
        assert adapter.fetch_cart("swiggy_somethingelse") is None

    def test_a_cart_that_moved_underneath_is_refused_by_the_engine(self, policy, policies, ledger):
        """The unpinned-snapshot problem, closed. `get_cart` is session-scoped
        with no etag, so the proposal names a hash and the engine refuses
        anything that no longer matches it."""
        before = payload("swiggy_cart")
        adapter = self.adapter({"get_cart": before})
        proposed = adapter.read_cart().cart

        after = json.loads(json.dumps(before))
        after["items"].append(
            {"displayName": "Smartwatch", "discountedFinalPrice": "15000", "quantity": 1}
        )
        moved = self.adapter({"get_cart": after})

        decision = decide(
            Proposal(policy.mandate_id, proposed.cart_id, 100),
            policies=policies,
            adapter=moved,
            ledger=ledger,
            now=NOW,
        )
        assert decision.verdict is Verdict.DENY
        assert "provenance.cart_not_found" in decision.reason_code

    def test_what_swiggy_quietly_changed_is_surfaced_not_swallowed(self):
        """An agent that asked for twelve things and got ten would otherwise
        reconcile perfectly against its own smaller bill."""
        cart = json.loads(json.dumps(payload("swiggy_cart")))
        cart["unserviceableItems"] = [{"displayName": "Cow Ghee 500ml"}]
        report = self.adapter({"get_cart": cart}).read_cart()
        assert report.diverged
        assert "Cow Ghee 500ml" in report.unserviceable

    def test_a_dead_session_yields_no_cart_rather_than_a_guess(self):
        def dead(_tool, **_params):
            raise ConnectionError("no session")

        assert SwiggyAdapter(dead, address_id="addr_1").fetch_cart("swiggy_x") is None


# --- the transport ----------------------------------------------------------


class TestSession:
    """Swiggy issues no API keys, tokens live five days and cannot be refreshed
    in v1.0. Every failure here has to say so, because the fix is always a
    person with a phone rather than a config change."""

    def client(self, monkeypatch, **env):
        from bounded_mandate import swiggy_mcp

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        # No sleeping in tests; the intervals exist for the live rate limit.
        monkeypatch.setattr(swiggy_mcp, "READ_INTERVAL", 0.0)
        monkeypatch.setattr(swiggy_mcp, "WRITE_INTERVAL", 0.0)
        return swiggy_mcp

    def test_no_token_says_what_to_actually_do(self, monkeypatch):
        mod = self.client(monkeypatch)
        monkeypatch.delenv("SWIGGY_ACCESS_TOKEN", raising=False)
        with pytest.raises(mod.SwiggySessionError, match="OAuth flow"):
            mod.SwiggyMCP(token="")("get_cart")

    def test_a_rejected_token_names_the_five_day_expiry(self, monkeypatch):
        mod = self.client(monkeypatch)

        class Rejected:
            status_code = 401
            headers: dict = {}
            text = "expired"
            content = b"expired"

        monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: Rejected())
        with pytest.raises(mod.SwiggySessionError, match="five days"):
            mod.SwiggyMCP(token="tok")("get_cart")

    def test_a_tool_answer_is_unwrapped_from_the_mcp_envelope(self, monkeypatch):
        """MCP wraps a tool result in `content[]`; the cart is the JSON inside."""
        mod = self.client(monkeypatch)
        assert mod._unwrap(
            {"content": [{"type": "text", "text": '{"items": [], "cartTotalAmount": "0"}'}]}
        ) == {"items": [], "cartTotalAmount": "0"}
        assert mod._unwrap({"structuredContent": {"items": []}}) == {"items": []}

    def test_an_unrecognised_envelope_yields_nothing_rather_than_a_guess(self, monkeypatch):
        """An empty payload becomes "no cart" upstream, which fails closed."""
        mod = self.client(monkeypatch)
        assert mod._unwrap({"content": [{"type": "image"}]}) == {}
        assert mod._unwrap("nonsense") == {}

    def test_an_sse_frame_decodes_like_a_json_body(self, monkeypatch):
        mod = self.client(monkeypatch)

        class Streamed:
            headers = {"Content-Type": "text/event-stream"}
            content = b"x"
            text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

        assert mod._parse(Streamed())["result"] == {"ok": True}

    def test_a_protocol_error_is_raised_not_returned(self, monkeypatch):
        mod = self.client(monkeypatch)
        with pytest.raises(mod.SwiggySessionError, match="-32601"):
            mod._result_of({"error": {"code": -32601, "message": "no such tool"}})

    def test_the_session_has_no_escape_hatch(self):
        """The adapter checks its allowlist before anything reaches here, so
        this class must not offer a second way in."""
        from bounded_mandate.swiggy_mcp import SwiggyMCP

        for wire in LIVE_WIRES:
            assert not hasattr(SwiggyMCP, wire)
        assert not hasattr(SwiggyMCP, "call_any")


# --- which shop is behind the seam ------------------------------------------


class TestBackendSelection:
    def rebuild(self, monkeypatch, value):
        import importlib

        from bounded_mandate import commerce

        monkeypatch.setenv("BM_COMMERCE", value)
        return importlib.reload(commerce)

    def test_mock_is_the_default(self, monkeypatch):
        import importlib

        from bounded_mandate import commerce

        monkeypatch.delenv("BM_COMMERCE", raising=False)
        mod = importlib.reload(commerce)
        assert mod.BACKEND == "mock"
        assert not mod.is_live()
        from bounded_mandate.merchant import Marketplace

        assert isinstance(mod.build(), Marketplace)

    def test_swiggy_is_one_variable_away(self, monkeypatch):
        mod = self.rebuild(monkeypatch, "swiggy")
        assert mod.is_live()
        assert isinstance(mod.build(), SwiggyAdapter)

    def test_an_unknown_backend_fails_loudly_rather_than_defaulting(self, monkeypatch):
        """Silently falling back to mock would mean a run you believed was live
        was not, which is the one way this could quietly lie."""
        mod = self.rebuild(monkeypatch, "instacart")
        with pytest.raises(RuntimeError, match="no such commerce backend"):
            mod.build()

    def test_both_backends_satisfy_the_only_method_the_engine_needs(self, monkeypatch):
        from bounded_mandate.merchant import Marketplace

        for backend in (Marketplace(), SwiggyAdapter(lambda *a, **k: {}, address_id="a")):
            assert callable(backend.fetch_cart)
            assert callable(backend.create_cart)
            assert callable(backend.search)


class TestFirewallHoldsThroughTheApp:
    def test_no_module_in_the_package_names_a_checkout_tool(self):
        """The adapter is not the only file that could grow a call to it."""
        import pathlib

        package = pathlib.Path(__file__).parent.parent / "bounded_mandate"
        offenders: list[str] = []
        for path in package.glob("*.py"):
            body = path.read_text()
            # Strip the module docstring, which explains why these are absent.
            parts = body.split('"""')
            code = "".join(parts[2:]) if len(parts) > 2 else body
            for wire in ("confirm_order", "place_food_order"):
                if f'"{wire}"' in code:
                    offenders.append(f"{path.name}:{wire}")
        assert not offenders, f"a checkout tool is named in: {offenders}"


# --- the money bug ----------------------------------------------------------


class TestFeesAreInsideTheCap:
    """`Cart.total_paise` sums item lines, and that is what the cap checks. A
    fee that never becomes a line is a charge the engine never sees."""

    FEE_CART = {
        "items": [
            {"displayName": "Aashirvaad Atta 5kg", "discountedFinalPrice": "1990", "quantity": 1}
        ],
        "billBreakdown": {
            "lineItems": [
                {"label": "Item total", "value": "1990"},
                {"label": "Delivery fee", "value": "60"},
            ],
            "toPay": "2050",
        },
        "selectedAddressDetails": {"address": "12 Nandidurga Rd, Bengaluru"},
    }

    def adapter(self, payload):
        return SwiggyAdapter(lambda tool, **_: payload, address_id="addr_1")

    def test_the_cart_adds_up_to_what_will_actually_be_charged(self):
        cart = self.adapter(self.FEE_CART).read_cart().cart
        assert cart.total_paise == 205_000, "the fee is outside the total the engine checks"

    def test_a_basket_under_the_cap_but_over_it_after_fees_is_caught(
        self, policy, policies, ledger
    ):
        """₹1,990 of groceries under a ₹2,000 cap, charged ₹2,050. Before the
        fee became a line the engine allowed this."""
        from tests.conftest import merchant_holding

        cart = self.adapter(self.FEE_CART).read_cart().cart
        decision = decide(
            Proposal(policy.mandate_id, cart.cart_id, cart.total_paise),
            policies=policies,
            adapter=merchant_holding(cart),
            ledger=ledger,
            now=NOW,
        )
        assert "cap.exceeded" in decision.reason_code
        assert decision.verdict is not Verdict.ALLOW

    def test_a_fee_line_does_not_read_as_an_off_scope_purchase(self, policy, policies, ledger):
        """Fees are the cost of the delivery already authorised, not a
        discretionary purchase — but they must not be a hole either, which the
        test above proves by still tripping the cap."""
        from tests.conftest import merchant_holding

        cart = self.adapter(self.FEE_CART).read_cart().cart
        fees = [i for i in cart.items if i.category == "fees"]
        assert fees, "the fee never became a line"
        assert all(i.category for i in cart.items), "a fee line must not read as unclassified"

        decision = decide(
            Proposal(policy.mandate_id, cart.cart_id, cart.total_paise),
            policies=policies,
            adapter=merchant_holding(cart),
            ledger=ledger,
            now=NOW,
        )
        assert "category.not_allowed" not in decision.reason_code
        assert "category.unknown" not in decision.reason_code

    def test_nothing_but_the_adapter_may_mint_a_fee_line(self):
        """A merchant that could get an item classified as `fees` would have
        found a category every policy allows."""
        for name in ("Delivery fee", "Handling fee", "fees", "Platform fee"):
            assert categorise(name) != "fees"

    def test_a_cart_whose_parts_do_not_sum_to_its_total_is_refused(self):
        """Independent of fees: a quantity mishandled or a line silently
        dropped leaves a cart that does not add up, and that is not a cart this
        engine should authorise."""
        broken = json.loads(json.dumps(self.FEE_CART))
        broken["billBreakdown"]["toPay"] = "9999"
        with pytest.raises(SwiggyUnavailable, match="does not add up"):
            self.adapter(broken).read_cart()


# --- category comes from the user, not the merchant or a model --------------


class TestCategoryFromTheList:
    CART = {
        "items": [
            {"displayName": "Nandini Good Life UHT", "discountedFinalPrice": "54", "quantity": 1}
        ],
        "billBreakdown": {"lineItems": [{"label": "Item total", "value": "54"}], "toPay": "54"},
        "selectedAddressDetails": {"address": "12 Nandidurga Rd, Bengaluru"},
    }
    SEARCH = {
        "products": [
            {
                "displayName": "Nandini Good Life UHT",
                "variations": [
                    {
                        "skuId": "S1",
                        "spinId": "SPIN-S1",
                        "quantityDescription": "",
                        "price": {"offerPrice": 54},
                        "isInStockAndAvailable": True,
                    }
                ],
            }
        ]
    }

    def adapter(self):
        return SwiggyAdapter(
            lambda tool, **_: self.SEARCH if tool == "search_products" else self.CART,
            address_id="addr_1",
        )

    def test_the_lookup_alone_would_have_stopped_this_order(self):
        """A brand-led name with no category word in it. This is the case that
        breaks the unattended run, and the reason the list exists."""
        assert categorise("Nandini Good Life UHT") == ""

    def test_the_users_classification_wins(self):
        cart = self.adapter().create_cart(
            ["Nandini Good Life UHT"], categories={"Nandini Good Life UHT": "groceries"}
        )
        assert cart.items[0].category == "groceries"

    def test_the_engine_can_still_refetch_the_cart_it_was_given(self):
        """The categories are part of the content hash, and `fetch_cart` has no
        list to consult — so they are pinned at build time. Without that the
        engine re-derives different ones and refuses its own cart."""
        adapter = self.adapter()
        built = adapter.create_cart(
            ["Nandini Good Life UHT"], categories={"Nandini Good Life UHT": "groceries"}
        )
        refetched = adapter.fetch_cart(built.cart_id)
        assert refetched is not None, "the engine could not refetch its own cart"
        assert refetched.items[0].category == "groceries"

    def test_an_unclassified_line_still_reaches_clarify(self):
        """Off-list items get the lookup, then nothing. Interrupting is the
        cheap failure; silently authorising the wrong thing is the expensive
        one."""
        cart = self.adapter().create_cart(["Nandini Good Life UHT"])
        assert cart.items[0].category == ""

    def test_no_agent_tool_takes_a_category_as_input(self):
        """The agent names *what* to buy. *What kind of thing* it is comes off a
        document it cannot write, so an injected prompt has nothing to argue
        with. Searching by category is fine — that is a query, not a claim."""
        from bounded_mandate.agent import TOOLS

        for tool in TOOLS:
            params = tool["function"].get("parameters", {}).get("properties", {})
            assert not [k for k in params if "categor" in k.lower()], (
                f"{tool['function']['name']} lets the agent assert a category"
            )


class TestMoneyFormatIsConfirmed:
    def test_rupees_with_paise_as_decimals(self):
        """Confirmed format, not an assumption: Swiggy returns `₹1500.15`."""
        assert to_paise("₹1500.15") == 150_015
        assert to_paise("1500.15") == 150_015
        assert to_paise(1500.15) == 150_015


# --- the shapes a real cart actually has ------------------------------------


class TestRealPayloadShapes:
    """Every case here was found by capturing a live cart, and every one of them
    was wrong in the synthetic fixture the tests previously passed against."""

    def cart(self):
        return json.loads((PAYLOADS / "swiggy_cart.json").read_text())

    def adapter(self, payload):
        return SwiggyAdapter(lambda tool, **_: payload, address_id="addr_1")

    def test_a_wrapped_total_is_read(self):
        """`toPay` is `{"label": "To Pay", "value": "₹159"}`, not a scalar."""
        assert self.adapter(self.cart()).read_cart().total_paise == 15_900

    def test_lines_name_themselves_with_item_name(self):
        """The cart says `itemName`; search says `displayName` for the same
        thing. Reading only the latter left every line blank — and a blank name
        classifies as nothing, so every real order would have CLARIFYed."""
        names = [i.name for i in self.adapter(self.cart()).read_cart().cart.items]
        assert all(names), f"a line has no name: {names}"
        assert any("Amul" in n for n in names)

    def test_free_delivery_does_not_break_the_bill(self):
        """ "Delivery Partner Fee" reads `FREE` on a real cart."""
        assert any(
            entry["value"] == "FREE" for entry in self.cart()["billBreakdown"]["lineItems"]
        ), "the captured cart no longer exercises this"
        self.adapter(self.cart()).read_cart()  # must not raise

    def test_the_rupee_rounding_is_carried_as_a_line(self):
        """Swiggy rounds `toPay` to the rupee while the lines carry paise, so a
        real cart is 40 paise short of its own total. Reconciled with a visible
        line rather than a tolerance — a tolerance is a hole you cannot see the
        size of."""
        report = self.adapter(self.cart()).read_cart()
        assert sum(i.price_paise for i in report.cart.items) == report.total_paise
        rounding = [i for i in report.cart.items if i.name == "Rounding"]
        assert rounding and abs(rounding[0].price_paise) < 100

    def test_a_residual_too_large_to_be_rounding_is_refused(self):
        payload = self.cart()
        payload["billBreakdown"]["toPay"]["value"] = "₹900"
        with pytest.raises(SwiggyUnavailable, match="does not add up"):
            self.adapter(payload).read_cart()

    def test_the_fees_on_a_real_cart_are_a_third_of_it(self):
        """₹116 of groceries, ₹159 charged. This is what "fees escape the cap"
        was worth in practice, and why it is the finding that mattered."""
        report = self.adapter(self.cart()).read_cart()
        goods = sum(i.price_paise for i in report.cart.items if i.category != "fees")
        assert goods == 11_600
        assert report.total_paise == 15_900
        assert report.total_paise - goods > goods * 0.35

    def test_the_cart_carries_the_address_id_not_the_prose(self):
        """The two endpoints format one address two different ways.

        `get_addresses` returns the composite Swiggy shows a person; `get_cart`
        returns only the street part. A policy pinned to either is refused
        against the other — and it fails as `delivery.unknown_address`, which is
        indistinguishable from an agent shipping somewhere it should not.

        Shape verified against a live account on 2026-08-26. The address book
        itself is not committed: it is a real name, phone number and home.
        """
        payload = self.cart()
        details = payload["selectedAddressDetails"]

        # What `get_addresses` calls the very same address.
        address_line = "<name>: <flat>, <area>, " + details["address"]
        assert address_line != details["address"], "the two forms are not the same string"

        cart = self.adapter(payload).read_cart().cart
        assert cart.delivery_address == details["id"]
        assert cart.delivery_address not in (address_line, details["address"])


class TestAnUnbilledItem:
    """Captured live on 2026-08-26: a festive rakhi nobody added, sitting in
    `items[]` at ₹89 and absent from `Item Total`.

    Two different residuals were being conflated into one ₹1 rounding
    tolerance, so a legitimate promotion was indistinguishable from a cart that
    does not add up — and the adapter refused the whole basket.
    """

    def cart(self):
        return json.loads((PAYLOADS / "swiggy_cart_promo.json").read_text())

    def adapter(self, payload):
        return SwiggyAdapter(lambda tool, **_: payload, address_id="addr_1")

    def test_the_captured_cart_really_does_carry_an_unbilled_item(self):
        payload = self.cart()
        listed = sum(
            (line.get("discountedFinalPrice") or line["mrp"]) * (line.get("quantity") or 1)
            for line in payload["items"]
        )
        billed = next(
            e for e in payload["billBreakdown"]["lineItems"] if e["label"] == "Item Total"
        )
        assert listed * 100 != to_paise(billed["value"]), "the fixture no longer exercises this"

    def test_it_reconciles_to_what_swiggy_will_charge(self):
        report = self.adapter(self.cart()).read_cart()
        assert sum(i.price_paise for i in report.cart.items) == report.total_paise
        assert report.total_paise == 15_500

    def test_the_free_thing_is_still_a_thing(self):
        """It stays in the cart as a line, so it is still categorised — and
        something the user did not ask for reaches them as a question rather
        than arriving in the box."""
        report = self.adapter(self.cart()).read_cart()
        rakhi = next(i for i in report.cart.items if "Rakhi" in i.name)
        assert rakhi.category == "", "an unasked-for item must not classify as anything"

        decision = decide(
            Proposal("mdt_1", report.cart.cart_id, report.total_paise),
            policies={
                "mdt_1": replace(
                    _grocery_policy(), delivery_addresses=frozenset({report.cart.delivery_address})
                )
            },
            adapter=_holding(report.cart),
            ledger=Ledger(Path(tempfile.mkdtemp()) / "ledger.jsonl"),
            now=NOW,
        )
        assert decision.verdict is Verdict.CLARIFY
        assert "category.unknown" in decision.reason_code

    def test_a_cart_that_genuinely_does_not_add_up_is_still_refused(self):
        """The tolerance was loosened for the goods, not for the bill."""
        payload = self.cart()
        payload["billBreakdown"]["toPay"]["value"] = "₹900"
        with pytest.raises(SwiggyUnavailable, match="does not add up"):
            self.adapter(payload).read_cart()


class TestUpdateCartNeedsBothIds:
    def test_an_offer_carries_both_identifiers(self):
        """`update_cart` requires `spinId` *and* `skuId`. Sending only the sku
        is accepted and silently adds nothing — the cart comes back empty and
        every later step blames itself."""
        search = json.loads((PAYLOADS / "swiggy_search.json").read_text())
        offers = SwiggyAdapter(lambda *a, **k: search, address_id="a").search("milk")
        assert offers
        assert all(o.sku_id and o.spin_id for o in offers)

    def test_a_variation_missing_either_id_is_skipped(self):
        """Better to not offer it than to offer something that cannot be added."""
        half = {
            "products": [
                {
                    "displayName": "Half a product",
                    "variations": [
                        {"skuId": "S1", "price": {"offerPrice": 10}, "isInStockAndAvailable": True}
                    ],
                }
            ]
        }
        assert SwiggyAdapter(lambda *a, **k: half, address_id="a").search("x") == []


class TestProductImages:
    """Swiggy returns product photography on both endpoints, public and free.

    It is carried as decoration and nothing else, so most of this file is about
    the things it must *not* touch.
    """

    def cart(self):
        return json.loads((PAYLOADS / "swiggy_cart.json").read_text())

    def search(self):
        return json.loads((PAYLOADS / "swiggy_search.json").read_text())

    def adapter(self, payload):
        return SwiggyAdapter(lambda tool, **_: payload, address_id="addr_1")

    # --- reading it ---------------------------------------------------------

    def test_a_real_cart_line_carries_its_photograph(self):
        items = self.adapter(self.cart()).read_cart().cart.items
        goods = [i for i in items if i.category != "fees"]
        assert goods and all(i.image_url.startswith("https://") for i in goods)

    def test_bill_lines_are_not_things_and_carry_no_photograph(self):
        """A handling fee has no picture. Neither does rounding."""
        items = self.adapter(self.cart()).read_cart().cart.items
        fees = [i for i in items if i.category == "fees"]
        assert fees and all(i.image_url == "" for i in fees)

    def test_search_offers_carry_one_too(self):
        offers = self.adapter(self.search()).search("milk")
        assert offers and all(o.image_url.startswith("https://") for o in offers)

    # --- sizing it ----------------------------------------------------------

    def test_the_url_is_resized_on_swiggys_cdn(self):
        """~600 KB per asset. A twelve-line cart would be 7 MB of photographs to
        render twelve thumbnails."""
        line = next(i for i in self.adapter(self.cart()).read_cart().cart.items if i.image_url)
        assert f"/image/upload/{THUMB}/" in line.image_url

    def test_an_unfamiliar_host_is_passed_through_untouched(self):
        """Degrade to a large image, never to a broken one."""
        assert _thumb("https://example.com/a.png") == "https://example.com/a.png"

    def test_transforming_twice_does_not_stack(self):
        once = _thumb("https://m.swiggy.com/image/upload/NI/a.png")
        assert _thumb(once) == once

    @pytest.mark.parametrize("missing", [None, "", "   ", 17, {}])
    def test_a_missing_url_is_empty_not_broken(self, missing):
        assert _thumb(missing) == ""

    # --- the three properties that keep it decoration -----------------------

    def test_a_photograph_does_not_change_what_a_cart_is(self):
        """`cart_id` is the content address the whole provenance check rests on.
        A merchant swapping a product shot must not invalidate an idempotency
        key or make the engine refuse a cart it already knew."""
        bare = [CartItem("Milk", 5_600, "groceries")]
        shot = [CartItem("Milk", 5_600, "groceries", image_url="https://x/y.png")]
        assert cart_id_for(bare, 5_600) == cart_id_for(shot, 5_600)

    def test_no_verdict_is_ever_reached_because_of_a_picture(self, policy, policies, ledger):
        from tests.conftest import merchant_holding

        def ruling(image):
            cart = Cart(
                cart_id=f"c_{bool(image)}",
                merchant="instamart",
                items=(CartItem("Milk", 5_600, "groceries", image_url=image),),
                delivery_address=next(iter(policy.delivery_addresses)),
            )
            return decide(
                Proposal(policy.mandate_id, cart.cart_id, 5_600),
                policies=policies,
                adapter=merchant_holding(cart),
                ledger=ledger,
                now=NOW,
            ).reason_code

        assert ruling("") == ruling("https://x/y.png") == "ok.in_policy"

    def test_the_agent_is_never_shown_a_product_image(self):
        """Merchant-controlled content handed to a model is an injection surface
        pointed at the one component least able to refuse it. The agent reads
        names and prices; pictures stop at the card."""
        import inspect

        from bounded_mandate.agent import BuyerAgent

        for tool in (BuyerAgent._search_catalog, BuyerAgent._create_cart):
            source = inspect.getsource(tool)
            assert "image" not in source.lower(), f"{tool.__name__} leaks an image to the model"


class TestNameResolution:
    """Two bugs found by ordering real snacks on the live shop."""

    def shop(self, offers):
        adapter = SwiggyAdapter(lambda tool, **_: {}, address_id="addr_1")
        adapter.search = lambda _q: offers  # type: ignore[method-assign]
        return adapter

    def offer(self, name, paise, sku="s1"):
        from bounded_mandate.swiggy import Offer

        return Offer(
            sku_id=sku, spin_id="p" + sku, name=name, price_paise=paise, category="", in_stock=True
        )

    def test_a_product_sharing_no_word_is_not_a_match(self):
        """Asked for "blue Lays x3" the old rule found no name containing the
        whole query, fell back to the cheapest of everything the search
        returned, and bought Too Yumm Veggie Stix.

        Silently returning something unrelated is worse than returning nothing:
        the reader gets a cart of things they did not ask for, and every bound
        the engine checks is satisfied by it.
        """
        shop = self.shop(
            [
                self.offer("Too Yumm Veggie Stix 60 g", 4_500),
                self.offer("Cadbury Dairy Milk 17 g", 1_900, sku="s2"),
            ]
        )
        assert shop._best_match("blue Lays x3") is None

    def test_the_cheapest_of_the_things_that_do_match(self):
        """Cheapest among plausible matches, not cheapest overall — an agent
        that asked for milk and got the premium pack has spent more of a cap
        than the reader expected."""
        shop = self.shop(
            [
                self.offer("Lays Magic Masala 52 g", 5_000),
                self.offer("Lays Cream Onion 25 g", 2_000, sku="s2"),
                self.offer("Bingo Mad Angles 40 g", 1_000, sku="s3"),
            ]
        )
        match = shop._best_match("blue Lays x3")
        assert match is not None
        assert match.name == "Lays Cream Onion 25 g"

    def test_pack_words_do_not_count_as_agreement(self):
        """ "x3" and "packet" are in every second product name, so counting them
        would make any two things look alike."""
        shop = self.shop([self.offer("Britannia Biscuits 1 packet x3", 3_000)])
        assert shop._best_match("blue Lays x3 packet") is None

    def test_asking_for_three_sends_a_quantity_not_three_lines(self):
        """The agent says "three packets" by naming the thing three times, and
        `update_cart` given the same skuId three times does not add three — it
        rejects the whole basket and answers with an empty cart, which surfaced
        as the unilluminating "cart carries no total"."""
        sent: dict = {}

        def call(tool, **params):
            if tool == "update_cart":
                sent.update(params)
                return {}
            if tool == "get_cart":
                return json.loads((PAYLOADS / "swiggy_cart.json").read_text())
            return {}

        adapter = SwiggyAdapter(call, address_id="addr_1")
        adapter.search = lambda _q: [self.offer("Lays Magic Masala 52 g", 5_000)]  # type: ignore[method-assign]
        adapter.create_cart(["blue Lays", "blue Lays", "blue Lays"])

        assert len(sent["items"]) == 1, "the same product was sent as three lines"
        assert sent["items"][0]["quantity"] == 3
