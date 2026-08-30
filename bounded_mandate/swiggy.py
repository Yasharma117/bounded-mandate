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
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .basket import Address
from .categories import FEES as FEES_CATEGORY
from .categories import categorise
from .engine import Cart, CartItem
from .merchant import UnknownItem

MERCHANT_NAME = "instamart"

#: Swiggy rounds `toPay` to the rupee while the bill lines carry paise, so a
#: real cart is a few paise short of its own total. That gap is reconciled with
#: a residual line rather than a tolerance, because a tolerance in a money check
#: is a hole you cannot see the size of. Anything larger than this is not
#: rounding and the cart is refused.
MAX_ROUNDING_PAISE = 100

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
    """See `_to_paise`; `FREE` is a real bill value meaning nothing to pay."""
    if isinstance(value, str) and value.strip().casefold() in {"free", "-", ""}:
        return 0
    return _to_paise(value)


def _to_paise(value: object) -> int:
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
    """One buyable variation.

    `update_cart` requires **both** ids, not just the sku. Sending only the sku
    is accepted and silently adds nothing, which is a worse failure than a
    rejection — the cart comes back empty and every later step blames itself.
    """

    sku_id: str
    spin_id: str
    name: str
    price_paise: int
    category: str
    in_stock: bool
    #: Already thumbnailed — see `_thumb`.
    image_url: str = ""


@dataclass(frozen=True)
class Variant:
    """One buyable pack of a product — the granularity `update_cart` accepts.

    `search` flattens these into separate `Offer`s, which is right for building
    a cart and wrong for a product page: it destroys the product→packs grouping
    that a size selector *is*.
    """

    sku_id: str
    spin_id: str
    #: The full name a list line would carry, pack included.
    name: str
    #: Just the pack, for the tile: "1 ltr x 4".
    label: str
    price_paise: int
    #: What it would cost without the offer. Equal to `price_paise` when there
    #: is no discount, so a struck-through price is never invented.
    mrp_paise: int
    #: Swiggy's own comparison, e.g. "8.5/100 ml" — the only honest way to
    #: compare a 500ml against a 1L against a six-pack.
    unit_price: str
    in_stock: bool
    image_url: str = ""

    @property
    def off(self) -> int:
        """Percent off, rounded. Zero when there is no discount."""
        if self.mrp_paise <= self.price_paise:
            return 0
        return round((1 - self.price_paise / self.mrp_paise) * 100)


@dataclass(frozen=True)
class Listing:
    """One product as a shop describes it, packs and all.

    Everything here is read from a payload we already fetch and discard. The
    mock supplies a thinner version of the same shape — no rating, no delivery
    estimate, one pack — so the sheet degrades rather than inventing fields.
    """

    name: str
    brand: str
    image_url: str
    variants: tuple[Variant, ...]
    # Which shop is speaking. Live this is always Instamart; on the mock it is
    # the field that keeps a seller's name out of the brand line.
    merchant: str = ""
    rating: str = ""
    rating_count: str = ""
    #: "18 MINS", as the shop says it.
    sla: str = ""
    #: `True`, `False`, or `None` when the shop does not classify it.
    veg: bool | None = None
    badges: tuple[str, ...] = ()

    @property
    def category(self) -> str:
        """Ours, not the shop's, and it fails closed — same rule as everywhere."""
        return categorise(self.name)


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


#: Words that describe a pack rather than a product, plus the quantity suffixes
#: the agent is told to put in a name ("blue Lays x3"). Dropped before matching,
#: or "x3" counts as something the request and the product have in common.
_NOISE = frozenset(
    {
        "x",
        "pack",
        "packet",
        "packets",
        "can",
        "cans",
        "bottle",
        "kg",
        "g",
        "gm",
        "ml",
        "l",
        "ltr",
        "litre",
        "pc",
        "pcs",
        "piece",
        "pieces",
        "of",
        "the",
        "a",
    }
)


def _words(text: str) -> frozenset[str]:
    """The distinctive words in a product name or a request."""
    return frozenset(
        word
        for raw in re.split(r"[^a-z0-9]+", text.casefold())
        if (word := raw.strip("0123456789")) and word not in _NOISE
    )


def _listing(raw: dict) -> Listing | None:
    """One product from a `search_products` row, packs intact."""
    base = str(raw.get("displayName") or raw.get("brand") or "").strip()
    variants: list[Variant] = []
    for variation in raw.get("variations") or []:
        sku, spin = variation.get("skuId"), variation.get("spinId")
        price = (variation.get("price") or {}).get("offerPrice")
        if not sku or not spin or price is None:
            continue
        pack = str(variation.get("quantityDescription") or "").strip()
        offer = to_paise(price)
        mrp = (variation.get("price") or {}).get("mrp")
        variants.append(
            Variant(
                sku_id=str(sku),
                spin_id=str(spin),
                name=f"{base} {pack}".strip(),
                label=pack or base,
                price_paise=offer,
                # Falls back to the price itself, so a struck-through figure is
                # never shown where there is no discount.
                mrp_paise=to_paise(mrp) if mrp is not None else offer,
                unit_price=str((variation.get("price") or {}).get("unitLevelPrice") or ""),
                in_stock=bool(variation.get("isInStockAndAvailable", True)),
                image_url=_thumb(variation.get("imageUrl")),
            )
        )
    if not base or not variants:
        return None

    first = (raw.get("variations") or [{}])[0]
    rating = first.get("rating") or {}
    sla = first.get("sla") or {}
    veg = first.get("vegClassifier")
    return Listing(
        name=base,
        brand=str(raw.get("brand") or ""),
        image_url=variants[0].image_url,
        variants=tuple(sorted(variants, key=lambda v: v.price_paise)),
        rating=str(rating.get("value") or ""),
        rating_count=str(rating.get("count") or ""),
        sla=f"{sla.get('value')} {sla.get('unit')}".strip() if sla.get("value") else "",
        veg=None if not veg else veg == "VEG_CLASSIFIER_VEG",
        badges=tuple(
            str(b.get("text"))
            for b in (raw.get("badges") or [])
            if isinstance(b, dict) and b.get("text")
        ),
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

    def addresses(self) -> list[Address]:
        """The user's address book, as the account holds it.

        `addressLine` is the composite Swiggy shows a person; the cart reports
        only the street part. Both are presentation, which is why `address_id`
        is what reaches the policy and these two only reach the card.
        """
        rows = self._invoke("get_addresses").get("addresses") or []
        return [
            Address(
                address_id=str(row.get("id") or row.get("addressId") or ""),
                label=str(row.get("addressTag") or row.get("addressCategory") or "Address"),
                line=str(row.get("addressLine") or ""),
            )
            for row in rows
            if row.get("id") or row.get("addressId")
        ]

    def use_address(self, address_id: str) -> None:
        """Where the next cart ships. A user decision, pushed down to the session."""
        self._address_id = address_id

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
                sku = variation.get("skuId")
                spin = variation.get("spinId")
                price = (variation.get("price") or {}).get("offerPrice")
                if not sku or not spin or price is None:
                    continue
                pack = variation.get("quantityDescription") or ""
                name = f"{base} {pack}".strip()
                offers.append(
                    Offer(
                        sku_id=str(sku),
                        spin_id=str(spin),
                        name=name,
                        price_paise=to_paise(price),
                        # Swiggy carries no category on either endpoint; this is
                        # ours, and it fails closed.
                        category=categorise(name),
                        in_stock=bool(variation.get("isInStockAndAvailable", True)),
                        image_url=_thumb(variation.get("imageUrl")),
                    )
                )
        return offers

    def describe(self, name: str) -> tuple[Listing | None, list[Listing]]:
        """One product with its packs, and the products Swiggy calls similar.

        A second read beside `search`, not a replacement for it. `search` is
        right for building a cart — one row per sku, because that is what
        `update_cart` takes — and exactly wrong for a product page, where the
        packs of one product are the thing being chosen between.

        Costs the same single `search_products` call `search` does.
        """
        payload = self._invoke("search_products", addressId=self.address_id(), query=name)
        products = [_listing(raw) for raw in (payload.get("products") or [])]
        products = [p for p in products if p is not None]
        if not products:
            return None, []

        wanted = _words(name)
        # The same rule the cart uses: most words in common, and never something
        # with none of them.
        chosen = max(products, key=lambda p: len(wanted & _words(p.name)))
        if wanted and not (wanted & _words(chosen.name)):
            return None, []

        similar = [_listing(raw) for raw in (payload.get("similarProducts") or [])]
        alternatives = [p for p in similar if p is not None and p.name != chosen.name]
        # The rest of the search is a better neighbour list than nothing when
        # Swiggy returns no `similarProducts` for a query.
        alternatives += [p for p in products if p.name != chosen.name]
        return chosen, alternatives[:12]

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
        #: sku -> (offer, quantity), in the order first asked for.
        #:
        #: Counted rather than repeated, because the agent says "three packets"
        #: by naming the thing three times, and `update_cart` given the same
        #: `skuId` three times does not add three — it rejects the whole basket
        #: and answers with an empty cart, which surfaced here as the
        #: unilluminating "cart carries no total".
        wanted: dict[str, tuple[Offer, int]] = {}
        resolved: dict[str, str] = {}
        swaps: list[tuple[str, str]] = []
        for name in item_names:
            match = self._best_match(name)
            if match is None:
                raise UnknownItem(name)
            offer, already = wanted.get(match.sku_id, (match, 0))
            wanted[match.sku_id] = (offer, already + 1)
            # Keyed by what Swiggy returned, not by what was asked for: the
            # classification belongs to the product that ended up in the cart.
            if requested.get(name):
                resolved[match.name] = requested[name]
            if match.name != name and (name, match.name) not in swaps:
                swaps.append((name, match.name))

        report = self.build_cart(list(wanted.values()), categories=resolved)
        self._substituted = tuple(swaps)
        return report.cart

    def _best_match(self, name: str) -> Offer | None:
        """The cheapest in-stock variation that is actually the thing asked for.

        Cheapest *among plausible matches*, not cheapest overall. An agent that
        asked for milk and got the premium two-litre pack has spent more of a cap
        than the user expected — but the older rule required the whole query to
        appear inside the product name, and fell back to the cheapest of
        everything the search returned when it did not.

        Live, "blue Lays x3" matched no name, so that fallback bought **Too Yumm
        Veggie Stix**. Silently returning something unrelated is worse than
        returning nothing: the user reads a cart of things they did not ask for,
        and every bound the engine checks is satisfied by it.

        So a candidate must share a distinctive word with the request. That is
        deliberately not a similarity score — this file already carries the note
        about why judging *whether a substitution matters* cannot be done by word
        counting. It is the much weaker claim that a product with no word in
        common is not a match at all, and no match is an honest answer.
        """
        offers = [o for o in self.search(name) if o.in_stock]
        if not offers:
            return None

        wanted = _words(name)
        if not wanted:
            return min(offers, key=lambda o: o.price_paise)

        def overlap(offer: Offer) -> int:
            return len(wanted & _words(offer.name))

        best = max(overlap(o) for o in offers)
        if best == 0:
            return None  # nothing here is the thing that was asked for
        return min((o for o in offers if overlap(o) == best), key=lambda o: o.price_paise)

    def build_cart(
        self, items: list[tuple[Offer, int]], *, categories: dict | None = None
    ) -> CartReport:
        """Replace the session cart with exactly these `(sku_id, quantity)`.

        Replacement is the documented behaviour of `update_cart`, so the adapter
        owns the whole basket rather than pretending it can append to one.
        """
        self._invoke(
            "update_cart",
            selectedAddressId=self.address_id(),
            items=[
                {"spinId": offer.spin_id, "skuId": offer.sku_id, "quantity": qty}
                for offer, qty in items
            ],
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
            # The cart calls it `itemName`; search calls the same thing
            # `displayName`. Both are checked because neither endpoint is
            # documented to carry the other's spelling.
            name = " ".join(
                part
                for part in (
                    line.get("itemName") or line.get("displayName") or line.get("name") or "",
                    line.get("itemVariant") or "",
                )
                if part
            ).strip()
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
                    image_url=_thumb(line.get("imageUrl")),
                )
            )

        # Swiggy lists an item at a price the bill does not necessarily charge.
        # A live cart arrived carrying a festive freebie nobody added — a ₹89
        # rakhi in `items[]`, absent from `Item Total`. Two different residuals
        # were being conflated into one ₹1 rounding tolerance, so a legitimate
        # promotion looked identical to a cart that does not add up.
        #
        # They are separated here. The goods reconcile against the bill's own
        # item total, which keeps every line visible at its listed price while
        # the arithmetic stays exact; what remains is rounding, and that keeps
        # its tight bound below.
        #
        # A free thing is still a thing: it stays in the cart as a line, so it
        # is still categorised, and something the user did not ask for still
        # reaches them as a question rather than arriving in the box.
        billed_goods = _item_total_from(payload)
        if billed_goods is not None:
            adjustment = billed_goods - sum(item.price_paise for item in items)
            if adjustment:
                items.append(
                    CartItem(
                        name="Discounts and free items" if adjustment < 0 else "Basket adjustment",
                        price_paise=adjustment,
                        category=FEES_CATEGORY,
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
        residual = total - charged
        if abs(residual) > MAX_ROUNDING_PAISE:
            raise SwiggyUnavailable(
                f"this cart does not add up: lines total ₹{charged / 100:,.2f}, "
                f"Swiggy will charge ₹{total / 100:,.2f}"
            )
        if residual:
            # `toPay` is the authority — it is what leaves the account — so the
            # cart is made to equal it rather than the other way round. Keeping
            # it as a visible line means the cap sees the real figure and the
            # card can show where the odd paise went.
            items.append(CartItem(name="Rounding", price_paise=residual, category=FEES_CATEGORY))
        # The **id**, never the prose. `get_addresses` and `get_cart` format the
        # same address two different ways — see `basket.Address` — so a policy
        # matched against the text is refused against its own address. The id is
        # byte-identical on both endpoints.
        details = payload.get("selectedAddressDetails") or {}
        address = details.get("id") or payload.get("selectedAddress")
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
        charge = to_paise(_amount(entry.get("value")))
        if charge == 0:
            continue  # a free delivery is not a line worth showing
        lines.append(CartItem(name=label, price_paise=charge, category=FEES_CATEGORY))
    return lines


#: Wide enough for a 44pt row at 3x, and nothing beyond that. The original
#: assets are ~600 KB each; a twelve-line cart would be 7 MB of photographs to
#: render twelve thumbnails.
THUMB = "w_160,h_160,c_fit"
_UPLOAD = "/image/upload/"


def _thumb(url: object) -> str:
    """A product photo, sized for a card.

    Swiggy's media host is Cloudinary-backed, so a transform segment in the path
    resizes on their CDN rather than ours — `w_160,h_160,c_fit` turns 594 KB
    into about 10 KB, measured.

    Done here rather than in the app so the client never learns the merchant's
    CDN scheme, the same reason `merchant_allowed` is computed server-side. A
    URL that does not carry the expected segment is passed through untouched: an
    unfamiliar host should degrade to a large image, never to a broken one.
    """
    if not isinstance(url, str) or not url.strip():
        return ""
    url = url.strip()
    if _UPLOAD not in url or THUMB in url:
        return url
    head, _, tail = url.partition(_UPLOAD)
    return f"{head}{_UPLOAD}{THUMB}/{tail}"


def _item_total_from(payload: dict) -> int | None:
    """What the bill charges for the goods.

    Distinct from what the item rows add up to, and the two differ whenever
    there is a discount or an unbilled item. `None` means the bill did not say,
    in which case there is nothing to reconcile against and the item rows stand.
    """
    breakdown = payload.get("billBreakdown") or {}
    for entry in breakdown.get("lineItems") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("label") or "").strip().casefold() in _ITEM_TOTAL_LABELS:
            return to_paise(_amount(entry.get("value")))
    return None


def _amount(value: object) -> object:
    """Swiggy wraps bill figures as `{"label": ..., "value": ...}`.

    Not documented on the reference page and not what the synthetic fixture
    guessed — found by reading a real cart, which is the entire reason for
    capturing one before trusting a parser.
    """
    if isinstance(value, dict):
        return value.get("value")
    return value


def _total_from(payload: dict) -> object:
    """`billBreakdown.toPay` when present, else the cart total."""
    breakdown = payload.get("billBreakdown") or {}
    if breakdown.get("toPay") is not None:
        return _amount(breakdown["toPay"])
    if payload.get("cartTotalAmount") is not None:
        return _amount(payload["cartTotalAmount"])
    raise SwiggyUnavailable("cart carries no total")


def _names(rows: object) -> tuple[str, ...]:
    if not isinstance(rows, list):
        return ()
    return tuple(
        str(row.get("displayName") or row.get("name") or "")
        for row in rows
        if isinstance(row, dict)
    )
