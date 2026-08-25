"""The user's shopping lists — what they want, and when.

This is the second thing the user owns, and it is not the policy. The policy
bounds *how much*; a list defines *what*, and its schedule says *when to try*.
Keeping them apart matters: raising a cap, adding an item and changing a cadence
are three different decisions, and one object holding all three would make every
edit look like a spending change.

**A schedule cannot widen authority.** It says when the agent should try, never
what it may do. A list set to run hourly under a mandate that permits one order
every four days will simply be refused on `frequency.exceeded` three times a
day — the engine does not consult the schedule, and there is no field here that
could make it. That is the whole reason scheduling is safe to hand to a user.

A list is a source of truth in the same sense the policy is: the user writes it,
the agent reads it, and **the agent has no tool that can change it**. An agent
that could redefine "usual" could then order the new definition entirely within
policy — an escalation that never trips a bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum


class ListKind(StrEnum):
    STANDING = "standing"
    """Repeats on a cadence, until paused."""
    ONCE = "once"
    """Runs a single time and is then spent. A birthday, a party, a one-off."""


@dataclass(frozen=True)
class ShoppingList:
    """A named list of catalog item names. Order is the user's, and preserved."""

    list_id: str
    name: str
    item_names: tuple[str, ...]
    #: What kind of thing each line is, as **the user** classified it.
    #:
    #: Swiggy returns no category on any endpoint, and the engine reads a blank
    #: category as CLARIFY — so on live data every line would stop and ask. The
    #: answer is not to guess: it is that the person who curated this list
    #: already knows what is on it. The list is a document the agent cannot
    #: write, which is exactly the property that makes it safe to be the source
    #: of *what kind of thing* as well as *what*.
    #:
    #: A user can of course mis-classify and widen their own scope. That is
    #: their authority, the same as editing their own cap, and it is written
    #: down somewhere they can read it.
    categories: dict[str, str] = field(default_factory=dict)
    kind: ListKind = ListKind.STANDING
    #: Standing lists only. `None` means the user has not set a cadence, so the
    #: list never runs on its own — it is a reference they order from by hand.
    every_days: int | None = None
    #: One-time lists only. The day it should go out.
    run_on: date | None = None
    #: When it last produced an order, whatever the verdict was.
    last_run_at: datetime | None = None
    paused: bool = False

    def category_of(self, item_name: str) -> str:
        """The user's classification for this line, or `""` if they never said."""
        return self.categories.get(item_name, "")

    def without(self, item_name: str) -> ShoppingList:
        return replace(
            self,
            item_names=tuple(n for n in self.item_names if n != item_name),
            categories={k: v for k, v in self.categories.items() if k != item_name},
        )

    def with_item(self, item_name: str) -> ShoppingList:
        """Appending an item the list already holds is a no-op, not a duplicate."""
        if item_name in self.item_names:
            return self
        return replace(self, item_names=(*self.item_names, item_name))

    @property
    def spent(self) -> bool:
        """A one-time list that has already gone out. It stays visible — a
        record of what was ordered is more use than a row that vanished."""
        return self.kind is ListKind.ONCE and self.last_run_at is not None

    def next_due(self, now: datetime | None = None) -> datetime | None:
        """When this list should next be attempted, or `None` if never.

        Takes the clock rather than reading it, so a schedule is testable and
        cannot quietly depend on wall time.
        """
        now = now or datetime.now(UTC)
        if self.paused or not self.item_names:
            return None
        if self.kind is ListKind.ONCE:
            if self.spent or self.run_on is None:
                return None
            return datetime.combine(self.run_on, datetime.min.time(), tzinfo=UTC)
        if self.every_days is None:
            return None
        if self.last_run_at is None:
            return now  # never run: due the moment anyone asks
        return self.last_run_at + timedelta(days=self.every_days)

    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        when = self.next_due(now)
        return when is not None and when <= now

    def ran(self, now: datetime | None = None) -> ShoppingList:
        return replace(self, last_run_at=now or datetime.now(UTC))


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
            list_id="usual",
            name="My usual groceries",
            item_names=USUAL_GROCERIES,
            categories=dict.fromkeys(USUAL_GROCERIES, "groceries"),
            kind=ListKind.STANDING,
            every_days=4,
        ),
        "breakfast": ShoppingList(
            list_id="breakfast",
            name="Breakfast top-up",
            item_names=("Toned milk 1L x2", "Eggs (12)", "Brown bread"),
            categories=dict.fromkeys(("Toned milk 1L x2", "Eggs (12)", "Brown bread"), "groceries"),
            kind=ListKind.STANDING,
            every_days=2,
        ),
    }
