"""Real Swiggy Instamart, behind the same one-method seam as the mock.

**This module cannot place an order.** Instamart checkout is COD-only, real, and
non-cancellable, with no test mode on Swiggy's side. `checkout` and
`confirm_order` are therefore not wrapped, not imported and not reachable — not
guarded, absent — and `tests/test_swiggy.py` fails the build if that stops being
true. The engine intercepts exactly where Swiggy's checkout would be and settles
on Razorpay instead, which is the whole argument: the authorization layer sits
between the cart and the payment.

## Why the cart id is a hash

`get_cart()` takes no arguments. It returns *the session's current cart*, which
`update_cart` replaces wholesale, and nothing in the API is documented to carry a
version or etag. So a cart id from Swiggy would be a session handle, and Layer 0
needs a reference the agent cannot influence and the engine can independently
verify.

The id is therefore the content itself:

    cart_id = "swiggy_" + sha256(lines ‖ total)[:16]

`fetch_cart(cart_id)` reads the live cart, recomputes the hash, and returns it
only on a match. If the basket moved between the agent proposing and the engine
verifying, the hash differs, the engine sees `None`, and the proposal dies as
`provenance.cart_not_found`. That is the unpinned-snapshot problem closed without
needing an etag, and it keeps the idempotency key meaningful — the same basket
really is the same id.

The MCP call is injected rather than imported so every line below it is testable
without a session, and so the set of tools this module can reach is visible in
one place.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .categories import FEES as FEES_CATEGORY
from .categories import categorise
from .engine import Cart, CartItem
from .merchant import UnknownItem

MERCHANT_NAME = "instamart"

#: Bill lines that are the goods themselves rather than a charge on top. Swiggy
#: itemises both in the same array.
_ITEM_TOTAL_LABELS = frozenset({"item total", "items total", "mrp total", "subtotal"})

#: Every tool this adapter is allowed to name. `checkout` and `confirm_order`
#: exist on the Instamart server and are deliberately not here; the firewall
#: test asserts this tuple against the module's source.
ALLOWED_TOOLS: tuple[str, ...] = (
    "get_addresses",
    "search_products",
    "update_cart",
    "get_cart",
    "clear_cart",
)

#: The delivery address the demo runs against. `search_products` requires an
#: `addressId` from `get_addresses` — it cannot be invented — and serviceability
#: varies by address, so it is pinned rather than discovered on camera.
ADDRESS_ID = os.environ.get("SWIGGY_ADDRESS_ID", "")

#: What the MCP layer looks like from in here: a tool name and its parameters.
CallTool = Callable[..., dict]


class SwiggyUnavailable(RuntimeError):
    """No session, or the server refused. Never a reason to guess a cart."""


# --- money ------------------------------------------------------------------
#
# `search_products` returns `mrp` and `offerPrice` as numbers; `get_cart` returns
# `mrp`, `discountedFinalPrice` and `cartTotalAmount` as strings. Neither page
# documents whether the unit is rupees or paise. So parsing lives in one place,
# is exact, and refuses what it cannot read — a float in a money path is how you
# lose a rupee and never find out which one.


def to_paise(value: object) -> int:
    """A Swiggy money field as integer paise.

    Accepts the number-or-string inconsistency between the two endpoints, and
    treats the value as **rupees**, which is what the captured payload shows.
    Anything with sub-paise precision is a parse failure rather than a rounding
    decision, because rounding is a decision this module has no right to make.
    """
    if isinstance(value, bool):  # bool is an int; nothing good comes of it here
        raise SwiggyUnavailable(f"not a money value: {value!r}")
    if isinstance(value, (int, float)):
        amount = Decimal(str(value))
    elif isinstance(value, str):
        cleaned = value.strip().replace("₹", "").replace(",", "")
        try:
            amount = Decimal(cleaned)
        except InvalidOperation as exc:
            raise SwiggyUnavailable(f"unparseable money value: {value!r}") from exc
    else:
        raise SwiggyUnavailable(f"unparseable money value: {value!r}")

    paise = amount * 100
    if paise != paise.to_integral_value():
        raise SwiggyUnavailable(f"sub-paise precision, refusing to round: {value!r}")
    return int(paise)


# --- what came back ---------------------------------------------------------


@dataclass(frozen=True)
class Offer:
    """One buyable variation. `sku_id` is what `update_cart` takes."""

    sku_id: str
    name: str
    price_paise: int
    category: str
    in_stock: bool


@dataclass(frozen=True)
class CartReport:
    """A cart, plus everything Swiggy quietly changed about it.

    The divergence fields are surfaced rather than swallowed: an agent that
    asked for twelve things and got ten is exactly what the provenance check
    exists to notice, and a cart that silently shrank would otherwise reconcile
    perfectly against its own smaller bill.
    """

    cart: Cart
    total_paise: int
    unserviceable: tuple[str, ...] = ()
    removed_out_of_stock: tuple[str, ...] = ()
    reduced_quantity: tuple[str, ...] = ()
    #: `asked for -> got`, whenever the resolved product name differs from the
    #: request. Reported flatly rather than judged.
    #:
    #: There is no rule here that decides whether the substitution matters,
    #: because there cannot be one: "Milk" resolving to "Amul Toned Milk 1 L"
    #: and to "Milk Frother Machine Deluxe Steel" are indistinguishable by name
    #: length, word overlap or any other cheap signal. A heuristic that gets
    #: that wrong is worse than none, because it manufactures confidence in the
    #: cases it fails on. So every rename is listed, the cart card shows what
    #: actually resolved, and the cap bounds what a bad substitution can cost.
    substituted: tuple[tuple[str, str], ...] = ()

    @property
    def diverged(self) -> bool:
        return bool(
            self.unserviceable
            or self.removed_out_of_stock
            or self.reduced_quantity
            or self.substituted
        )


def cart_id_for(items: list[CartItem], total_paise: int) -> str:
    """Content-address a cart, so its id survives having no etag.

    Order-independent, because the merchant is free to return lines in whatever
    order it likes and that is not a change to what is in the basket.
    """
    lines = sorted(f"{item.name}|{item.price_paise}|{item.category}" for item in items)
    seed = json.dumps({"lines": lines, "total": total_paise}, sort_keys=True)
    return "swiggy_" + hashlib.sha256(seed.encode()).hexdigest()[:16]


class SwiggyAdapter:
    """Instamart, read and cart-assembly only.

    Satisfies `CommerceAdapter` through `fetch_cart` and nothing else, which is
    the only method `engine.py` has ever needed.
    """

    def __init__(self, call: CallTool, *, address_id: str | None = None) -> None:
        self._call = call
        self._address_id = address_id or ADDRESS_ID
        #: What the user's list said about the lines in the current cart, pinned
        #: when the cart was built.
        #:
        #: `fetch_cart` is the engine's path and has no list to consult — it is
        #: handed a cart id and nothing else. Without this it would re-derive
        #: categories from the substring lookup, get different ones than the
        #: build did, hash to a different id, and refuse the engine's own cart.
        #: Pinning them means the classification the user asserted is the one
        #: the policy is checked against.
        self._assigned: dict[str, str] = {}
        #: Where the product that ended up in the cart is not the product that
        #: was asked for.
        self._substituted: tuple[tuple[str, str], ...] = ()

    # --- agent-facing -------------------------------------------------------

    def address_id(self) -> str:
        """The pinned address, or the first one the account has."""
        if self._address_id:
            return self._address_id
        addresses = self._invoke("get_addresses").get("addresses") or []
        if not addresses:
            raise SwiggyUnavailable("this account has no delivery address")
        self._address_id = str(addresses[0].get("id") or addresses[0].get("addressId"))
        return self._address_id

    def search(self, query: str) -> list[Offer]:
        """Find buyable variations. One row per `skuId`, because that is the
        granularity `update_cart` accepts — a product with three pack sizes is
        three different things you can buy."""
        payload = self._invoke("search_products", addressId=self.address_id(), query=query)
        offers: list[Offer] = []
        for product in payload.get("products") or []:
            base = product.get("displayName") or product.get("brand") or ""
            for variation in product.get("variations") or []:
                sku = variation.get("skuId") or variation.get("spinId")
                price = (variation.get("price") or {}).get("offerPrice")
                if not sku or price is None:
                    continue
                pack = variation.get("quantityDescription") or ""
                name = f"{base} {pack}".strip()
                offers.append(
                    Offer(
                        sku_id=str(sku),
                        name=name,
                        price_paise=to_paise(price),
                        # Swiggy carries no category on either endpoint; this is
                        # ours, and it fails closed.
                        category=categorise(name),
                        in_stock=bool(variation.get("isInStockAndAvailable", True)),
                    )
                )
        return offers

    def create_cart(
        self,
        item_names: list[str],
        *,
        delivery_address: str = "",
        merchant: str = MERCHANT_NAME,
        categories: dict | None = None,
    ) -> Cart:
        """Build a cart from item *names*, which is what the agent's tool takes.

        The agent says "Toned milk 1L"; `update_cart` wants a `skuId`. Resolving
        that gap here is the point of an adapter — `agent.py` never learns which
        merchant is behind the seam, and the same tool contract drives the mock
        and the real shop.

        `delivery_address` is accepted and ignored: Swiggy delivers to the
        address on the session, and the cart reports back which one that was.
        Letting a caller pass an address here would invent an authority this
        adapter does not have.
        """
        requested = categories or {}
        chosen: list[tuple[str, int]] = []
        resolved: dict[str, str] = {}
        swaps: list[tuple[str, str]] = []
        for name in item_names:
            match = self._best_match(name)
            if match is None:
                raise UnknownItem(name)
            chosen.append((match.sku_id, 1))
            # Keyed by what Swiggy returned, not by what was asked for: the
            # classification belongs to the product that ended up in the cart.
            if requested.get(name):
                resolved[match.name] = requested[name]
            if match.name != name:
                swaps.append((name, match.name))

        report = self.build_cart(chosen, categories=resolved)
        self._substituted = tuple(swaps)
        return report.cart

    def _best_match(self, name: str) -> Offer | None:
        """Cheapest in-stock variation whose name contains the query.

        Cheapest rather than closest: an agent that asked for milk and got the
        premium two-litre pack has spent more of a cap than the user expected,
        and the engine would be right to escalate something the adapter chose.
        """
        offers = [o for o in self.search(name) if o.in_stock]
        if not offers:
            return None
        wanted = name.casefold()
        exact = [o for o in offers if wanted in o.name.casefold()]
        return min(exact or offers, key=lambda o: o.price_paise)

    def build_cart(
        self, items: list[tuple[str, int]], *, categories: dict | None = None
    ) -> CartReport:
        """Replace the session cart with exactly these `(sku_id, quantity)`.

        Replacement is the documented behaviour of `update_cart`, so the adapter
        owns the whole basket rather than pretending it can append to one.
        """
        self._invoke(
            "update_cart",
            selectedAddressId=self.address_id(),
            items=[{"skuId": sku, "quantity": qty} for sku, qty in items],
        )
        self._assigned = dict(categories or {})
        return self.read_cart()

    def clear(self) -> None:
        self._invoke("clear_cart")
        self._assigned = {}

    # --- engine-facing ------------------------------------------------------

    def read_cart(self, categories: dict[str, str] | None = None) -> CartReport:
        """The canonical cart, as Swiggy currently holds it.

        `categories` is the user's own classification, keyed by the name they
        asked for — see `create_cart`. It wins over the substring lookup, and
        the lookup wins over nothing at all.
        """
        payload = self._invoke("get_cart")
        assigned = self._assigned if categories is None else categories
        items: list[CartItem] = []
        for line in payload.get("items") or []:
            name = line.get("displayName") or line.get("name") or ""
            price = line.get("discountedFinalPrice")
            if price is None:
                price = line.get("mrp")
            unit = to_paise(price)
            # A line is a quantity of a thing; the engine sums line totals.
            quantity = int(line.get("quantity") or 1)
            items.append(
                CartItem(
                    name=name,
                    price_paise=unit * quantity,
                    category=_category_for(name, assigned),
                )
            )

        total = to_paise(_total_from(payload))

        # Fees become lines, because the cap checks the sum of lines and the
        # user is charged `toPay`. Left out, delivery and handling are money the
        # engine never sees — a basket under the cap, charged over it.
        items.extend(_fee_lines(payload))

        if not items:
            raise SwiggyUnavailable("the cart is empty; there is nothing to authorise")

        negative = [i.name for i in items if i.price_paise < 0 and i.category != FEES_CATEGORY]
        if negative:
            # A discount belongs on the bill, not on a line of goods. An item
            # priced below zero exists only to pull a total under a cap.
            raise SwiggyUnavailable(f"item priced below zero: {', '.join(negative)}")

        charged = sum(item.price_paise for item in items)
        if charged != total:
            raise SwiggyUnavailable(
                f"this cart does not add up: lines total ₹{charged / 100:,.2f}, "
                f"Swiggy will charge ₹{total / 100:,.2f}"
            )
        address = (payload.get("selectedAddressDetails") or {}).get("address") or payload.get(
            "selectedAddress"
        )
        cart = Cart(
            cart_id=cart_id_for(items, total),
            merchant=MERCHANT_NAME,
            items=tuple(items),
            delivery_address=str(address or ""),
        )
        return CartReport(
            cart=cart,
            total_paise=total,
            substituted=self._substituted,
            unserviceable=_names(payload.get("unserviceableItems")),
            removed_out_of_stock=_names(payload.get("removedOutOfStockItems")),
            reduced_quantity=_names(payload.get("reducedQuantityItems")),
        )

    def fetch_cart(self, cart_id: str) -> Cart | None:
        """The whole `CommerceAdapter` contract.

        Reads the live cart and returns it **only if it still hashes to the id
        the proposal named**. A basket that moved in between is not the basket
        that was proposed, and the engine is right to refuse it.
        """
        try:
            report = self.read_cart()
        except SwiggyUnavailable:
            return None
        return report.cart if report.cart.cart_id == cart_id else None

    # --- the only place a tool name is spoken -------------------------------

    def _invoke(self, tool: str, **params: object) -> dict:
        if tool not in ALLOWED_TOOLS:
            raise SwiggyUnavailable(f"{tool} is not a tool this adapter may call")
        try:
            result = self._call(tool, **params)
        except Exception as exc:
            raise SwiggyUnavailable(f"{tool} failed: {exc}") from exc
        return result if isinstance(result, dict) else {}


def _category_for(name: str, assigned: dict[str, str]) -> str:
    """List first, lookup second, blank last.

    Only the first is authoritative. The user curated the list; the lookup is a
    convenience for things they never named; and blank makes the engine ask,
    which is the right answer when nobody has actually said.

    The match is **exact on the resolved product name**, not a substring. It
    used to be a substring, which meant a list line "Milk" classified as
    groceries also classified "Milk Chocolate Bar" — and since the adapter picks
    the cheapest product matching a query, a merchant could name something to
    inherit a category the user never granted it. `create_cart` keys this map by
    what Swiggy actually returned, so there is nothing left to guess.
    """
    category = assigned.get(name, "")
    # Only the adapter mints a fee line. A user-supplied `fees` would be a
    # category every policy allows, granted to arbitrary goods.
    if category and category != FEES_CATEGORY:
        return category
    return categorise(name)


def _fee_lines(payload: dict) -> list[CartItem]:
    """Everything on the bill that is a charge rather than a good.

    Swiggy itemises fees alongside the item total in the same array, so the
    labels are Swiggy's own and the card can print "Delivery fee ₹35" as a line
    the reader recognises.
    """
    breakdown = payload.get("billBreakdown") or {}
    lines: list[CartItem] = []
    for entry in breakdown.get("lineItems") or []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not label or label.casefold() in _ITEM_TOTAL_LABELS:
            continue
        charge = to_paise(entry.get("value"))
        if charge == 0:
            continue  # a free delivery is not a line worth showing
        lines.append(CartItem(name=label, price_paise=charge, category=FEES_CATEGORY))
    return lines


def _total_from(payload: dict) -> object:
    """`billBreakdown.toPay` when present, else the cart total."""
    breakdown = payload.get("billBreakdown") or {}
    if breakdown.get("toPay") is not None:
        return breakdown["toPay"]
    if payload.get("cartTotalAmount") is not None:
        return payload["cartTotalAmount"]
    raise SwiggyUnavailable("cart carries no total")


def _names(rows: object) -> tuple[str, ...]:
    if not isinstance(rows, list):
        return ()
    return tuple(
        str(row.get("displayName") or row.get("name") or "")
        for row in rows
        if isinstance(row, dict)
    )
