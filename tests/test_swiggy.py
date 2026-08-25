"""Real Instamart, behind the same one-method seam.

The first class here is the one that matters most. Instamart checkout is
COD-only, real, and non-cancellable — there is no test mode on Swiggy's side —
so the property under test is that no code path reaches it. That test is
load-bearing: it is what stops a future edit from helpfully wiring checkout.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from bounded_mandate import Proposal, Verdict, decide
from bounded_mandate.categories import categorise
from bounded_mandate.engine import CartItem
from bounded_mandate.swiggy import (
    ALLOWED_TOOLS,
    SwiggyAdapter,
    SwiggyUnavailable,
    cart_id_for,
    to_paise,
)
from tests.conftest import NOW

PAYLOADS = Path(__file__).parent / "payloads"

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
        for bad in ("", "free", None, True, {}):
            with pytest.raises(SwiggyUnavailable):
                to_paise(bad)


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
