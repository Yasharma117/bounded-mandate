"""Lists, and when they are due.

The property that matters most is the one at the bottom: a schedule says when
the agent should *try*, and can never change what it is *allowed* to do.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from bounded_mandate import Cart, CartItem, Proposal, Verdict, decide
from bounded_mandate.basket import ListKind, ShoppingList, seed_lists
from tests.conftest import HOME, NOW, merchant_holding

MILK = ("Toned milk 1L x2",)


def standing(**kwargs) -> ShoppingList:
    return ShoppingList(list_id="l", name="L", item_names=MILK, every_days=4, **kwargs)


def test_a_standing_list_that_has_never_run_is_due_now():
    assert standing().due(NOW)


def test_a_standing_list_waits_out_its_cadence():
    ran = standing().ran(NOW)
    assert not ran.due(NOW + timedelta(days=3, hours=23))
    assert ran.due(NOW + timedelta(days=4))


def test_a_standing_list_with_no_cadence_never_fires_on_its_own():
    """A list is useful as a reference even when nothing is scheduled. The
    absence of a cadence must mean 'never', not 'immediately'."""
    reference = ShoppingList(list_id="l", name="L", item_names=MILK, every_days=None)
    assert reference.next_due() is None
    assert not reference.due(NOW)


def test_a_one_time_list_is_due_on_its_day_and_spent_after():
    once = ShoppingList(
        list_id="party",
        name="Party",
        item_names=MILK,
        kind=ListKind.ONCE,
        run_on=NOW.date(),
    )
    assert once.due(NOW)
    assert not once.spent

    after = once.ran(NOW)
    assert after.spent
    assert not after.due(NOW + timedelta(days=365))
    # It stays visible: a record of what was ordered beats a row that vanished.
    assert after.item_names == MILK


def test_a_one_time_list_is_not_due_before_its_day():
    once = ShoppingList(
        list_id="party",
        name="Party",
        item_names=MILK,
        kind=ListKind.ONCE,
        run_on=(NOW + timedelta(days=3)).date(),
    )
    assert not once.due(NOW)
    assert once.due(NOW + timedelta(days=3))


def test_pausing_stops_a_list_without_forgetting_it():
    from dataclasses import replace

    running = standing().ran(NOW - timedelta(days=30))
    assert running.due(NOW)
    assert not replace(running, paused=True).due(NOW)


def test_an_empty_list_is_never_due():
    """Otherwise a list the user emptied would keep proposing nothing."""
    assert not ShoppingList(list_id="l", name="L", item_names=(), every_days=1).due(NOW)


def test_seeded_lists_are_distinct_and_schedulable():
    lists = seed_lists()
    assert len(lists) >= 2
    assert {shopping.list_id for shopping in lists.values()} == set(lists)
    assert all(shopping.every_days for shopping in lists.values())


# --- the property that makes scheduling safe to hand to a user ---------------


def test_a_schedule_cannot_widen_what_the_engine_permits(policy, policies, ledger):
    """A list set to run hourly under a mandate permitting one order every four
    days is simply refused, three times a day. The engine never reads the
    schedule, and there is no field on a list that could reach it."""
    hourly = ShoppingList(list_id="greedy", name="Greedy", item_names=MILK, every_days=0)
    assert hourly.every_days == 0  # as aggressive as the type allows

    def propose(cart_id: str, at: datetime):
        cart = Cart(
            cart_id=cart_id,
            merchant="instamart",
            items=(CartItem("Toned milk 1L x2", 7_000, "groceries"),),
            delivery_address=HOME,
        )
        return decide(
            Proposal(policy.mandate_id, cart_id, 7_000),
            policies=policies,
            adapter=merchant_holding(cart),
            ledger=ledger,
            now=at,
        )

    # The policy allows two charges per seven days. A schedule cannot buy a third.
    assert propose("cart_1", NOW).verdict is Verdict.ALLOW
    assert propose("cart_2", NOW).verdict is Verdict.ALLOW
    third = propose("cart_3", NOW)
    assert third.verdict is not Verdict.ALLOW
    assert "frequency.exceeded" in third.reason_code
