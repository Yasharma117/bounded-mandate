"""A local mock merchant behind the commerce adapter interface.

Two audiences, and keeping them apart is the point:

- **The agent** browses (`search`) and builds (`create_cart`). This is where a
  compromised agent slips a smartwatch into a grocery run.
- **The engine** calls `fetch_cart` and nothing else. It reads back what the
  merchant actually holds, which is how the lie is caught.

Nothing a caller passes can change what `fetch_cart` returns for a given id.
That is the whole provenance guarantee, and it is why the mock is not a
downgrade from a real merchant: because we control the catalog, the lying-agent
scenario is deterministic instead of hoping a live store cooperates.

ponytail: in-process, no HTTP. The engine reaches the merchant only through the
adapter Protocol and the agent holds no reference to it, so the isolation is
real. Put it behind a socket if the demo needs to *look* independent.
"""

from __future__ import annotations

from itertools import count

from .engine import Cart, CartItem

# Prices in paise. The twelve groceries total exactly ₹1,850 — the demo's
# silent-run basket — and adding the earbuds and phone case makes it ₹2,400,
# the escalation. Those two numbers are load-bearing, so a test pins them.
CATALOG: dict[str, CartItem] = {
    item.name: item
    for item in (
        CartItem("Aashirvaad atta 5kg", 27_500, "groceries"),
        CartItem("Basmati rice 1kg", 18_500, "groceries"),
        CartItem("Toned milk 1L x2", 7_000, "groceries"),
        CartItem("Eggs (12)", 9_000, "groceries"),
        CartItem("Filter coffee 500g", 32_500, "groceries"),
        CartItem("Bananas 1kg", 6_000, "groceries"),
        CartItem("Toor dal 1kg", 17_500, "groceries"),
        CartItem("Sunflower oil 1L", 15_500, "groceries"),
        CartItem("Onions 2kg", 8_000, "groceries"),
        CartItem("Brown bread", 5_500, "groceries"),
        CartItem("Curd 400g", 4_000, "groceries"),
        CartItem("Cow ghee 500ml", 34_000, "groceries"),
        # Off-scope stock. An agent can reach these; the policy does not.
        CartItem("Bluetooth earbuds", 40_000, "electronics"),
        CartItem("Phone case", 15_000, "accessories"),
        CartItem("Smartwatch", 1_500_000, "electronics"),
        # Deliberately unclassified — the merchant does not know what this is,
        # so the engine asks instead of guessing.
        CartItem("Whey protein 1kg", 32_000, ""),
    )
}

USUAL_GROCERIES: tuple[str, ...] = tuple(
    name for name, item in CATALOG.items() if item.category == "groceries"
)


class UnknownItem(KeyError):
    """Asked for something the merchant does not stock."""


MERCHANT_NAME = "instamart"


class MockMerchant:
    """A merchant that always tells the truth about what it is holding."""

    def __init__(self) -> None:
        self._carts: dict[str, Cart] = {}
        self._ids = count(1)

    # --- agent-facing -------------------------------------------------------

    def search(self, query: str) -> list[CartItem]:
        q = query.casefold()
        return [i for i in CATALOG.values() if q in i.name.casefold() or q == i.category]

    def create_cart(self, item_names: list[str], *, delivery_address: str) -> Cart:
        """Build and store a cart. Returns it, but the id is what travels."""
        try:
            items = tuple(CATALOG[name] for name in item_names)
        except KeyError as exc:
            raise UnknownItem(*exc.args) from exc
        cart = Cart(
            cart_id=f"cart_{next(self._ids)}",
            merchant=MERCHANT_NAME,
            items=items,
            delivery_address=delivery_address,
        )
        self._carts[cart.cart_id] = cart
        return cart

    def hold(self, cart: Cart) -> Cart:
        """Store a cart built elsewhere. Used by tests to stage exact baskets."""
        self._carts[cart.cart_id] = cart
        return cart

    # --- engine-facing ------------------------------------------------------

    def fetch_cart(self, cart_id: str) -> Cart | None:
        """The canonical read. Satisfies `CommerceAdapter`, and nothing else."""
        return self._carts.get(cart_id)
