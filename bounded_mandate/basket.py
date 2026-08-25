"""The user's shopping list — what "my usual groceries" actually means.

This is the second thing the user owns, and it is not the policy. The policy
bounds *how much*; the list defines *what*. Keeping them apart matters: raising
a cap and adding an item are different decisions, and collapsing them into one
object would make every edit look like a spending change.

The list is a source of truth in the same sense the policy is — the user writes
it, the agent reads it, and **the agent has no tool that can change it**. That
asymmetry is the whole point. An agent that could edit the list could quietly
redefine "usual" and then order it entirely within policy, which is exactly the
attack the engine exists to stop, arriving through a door nobody was watching.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ShoppingList:
    """A named list of catalog item names. Order is the user's, and preserved."""

    list_id: str
    name: str
    item_names: tuple[str, ...]

    def without(self, item_name: str) -> ShoppingList:
        return replace(self, item_names=tuple(n for n in self.item_names if n != item_name))

    def with_item(self, item_name: str) -> ShoppingList:
        """Appending an item the list already holds is a no-op, not a duplicate."""
        if item_name in self.item_names:
            return self
        return replace(self, item_names=(*self.item_names, item_name))


# The weekly basket, seeded. Named explicitly rather than derived from the
# catalog: the catalog also stocks a groceries-categorised item carrying a
# prompt injection, and "everything filed as groceries" is exactly the wrong
# definition of a usual basket.
USUAL_GROCERIES: tuple[str, ...] = (
    "Aashirvaad atta 5kg",
    "Basmati rice 1kg",
    "Toned milk 1L x2",
    "Eggs (12)",
    "Filter coffee 500g",
    "Bananas 1kg",
    "Toor dal 1kg",
    "Sunflower oil 1L",
    "Onions 2kg",
    "Brown bread",
    "Curd 400g",
    "Cow ghee 500ml",
)


def seed_lists() -> dict[str, ShoppingList]:
    return {
        "usual": ShoppingList(
            list_id="usual", name="My usual groceries", item_names=USUAL_GROCERIES
        )
    }
