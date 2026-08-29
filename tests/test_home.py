"""The home screen's state, which is the engine's state said out loud.

The point of this surface is that **nobody was present**. An order goes out at
9am, the engine rules, the ledger records — and if the reader was not in the
thread at that moment, nothing ever told them. So the thing worth testing is
which single fact home leads with, and in what order it prefers them.
"""

from __future__ import annotations

import re

from bounded_mandate import web
from bounded_mandate.basket import USUAL_GROCERIES
from bounded_mandate.wording import ACTIONS, CHIPS

USUAL = list(USUAL_GROCERIES)
OVER = [*USUAL, "Bluetooth earbuds", "Phone case"]


def home(client) -> dict:
    return client.get("/api/home").json()


def acts(payload: dict) -> list[str]:
    return [a["id"] for a in payload["actions"]]


# --- one fact at a time -------------------------------------------------------


def test_a_list_about_to_run_says_there_is_nothing_to_do(client):
    """The most reassuring screen in the set, and the one we never had."""
    out = home(client)
    assert out["state"] == "preflight"
    assert "nothing for you to do" in out["detail"].lower()
    assert acts(out) == ["pause", "view_basket"]


def test_an_order_that_already_ran_leads_with_the_receipt(client):
    client.post("/api/proposal", json={"items": USUAL, "claimed_total_paise": 185_000})
    out = home(client)

    assert out["state"] == "ruled"
    assert "1,850" in out["headline"]
    # It says so plainly, because the reader was not there.
    assert "while you were away" in out["detail"]


def test_an_escalation_offers_routes_and_takes_none(client):
    client.post("/api/proposal", json={"items": OVER, "claimed_total_paise": 240_000})
    out = home(client)

    assert out["state"] == "needs_you"
    assert acts(out) == ["approve_once", "drop_flagged", "not_now"]
    # The reasons travel, so the card can say *which* two items.
    assert "Bluetooth earbuds" in out["detail"]


def test_a_refusal_offers_no_way_to_approve_it(client):
    """An agent caught misreporting its own basket is not a thing to wave
    through with one tap. The absence is the point."""
    client.post(
        "/api/proposal", json={"items": [*USUAL, "Smartwatch"], "claimed_total_paise": 185_000}
    )
    out = home(client)

    assert out["state"] == "needs_you"
    assert acts(out) == ["see_attempt"]
    assert "approve_once" not in acts(out)


def test_an_unclassifiable_line_asks_rather_than_guesses(client):
    client.post(
        "/api/proposal", json={"items": ["Whey protein 1kg"], "claimed_total_paise": 32_000}
    )
    out = home(client)

    assert out["state"] == "needs_you"
    assert acts(out) == ["classify", "approve_once", "leave_out"]


def test_paused_lists_leave_the_rule_simply_running(client):
    for list_id in list(web.LISTS):
        client.put(f"/api/list/{list_id}/schedule", json={"paused": True})
    out = home(client)

    assert out["state"] == "at_rest"
    assert acts(out) == ["view_rule", "pause"]


# --- the halt -----------------------------------------------------------------


def test_moving_the_address_halts_a_basket_staged_for_the_old_one(client):
    """Nola calls this auto-halt on recipient change. Ours is the same bound:
    a grant may widen what and how much, never where."""
    escalated = client.post(
        "/api/proposal", json={"items": ["Smartwatch"], "claimed_total_paise": 1_500_000}
    ).json()
    client.put("/api/address", json={"address_id": "office"})

    refused = client.post("/api/mandate/one-time", json={"cart_id": escalated["cart_id"]})
    assert refused.status_code == 403

    out = home(client)
    assert out["state"] == "needs_you"
    assert "authorised" in out["headline"]
    assert acts(out) == ["reauthorise", "cancel_basket"]


def test_the_halt_is_recorded_not_only_returned(client):
    """A 403 the app swallows would leave the reader with nothing to look at."""
    escalated = client.post(
        "/api/proposal", json={"items": ["Smartwatch"], "claimed_total_paise": 1_500_000}
    ).json()
    client.put("/api/address", json={"address_id": "office"})
    client.post("/api/mandate/one-time", json={"cart_id": escalated["cart_id"]})

    events = [e.payload.get("event") for e in web.LEDGER.entries()]
    assert "HALTED" in events


# --- precedence ---------------------------------------------------------------


def test_something_waiting_on_you_outranks_something_about_to_happen(client):
    """A list is due the whole time. An escalation still wins the slot."""
    assert home(client)["state"] == "preflight"
    client.post("/api/proposal", json={"items": OVER, "claimed_total_paise": 240_000})
    assert home(client)["state"] == "needs_you"


def test_dismissing_hands_the_slot_back(client):
    client.post("/api/proposal", json={"items": OVER, "claimed_total_paise": 240_000})
    key = home(client)["decision"]["idempotency_key"]

    client.post("/api/home/seen", json={"idempotency_key": key})

    assert home(client)["state"] == "preflight"


def test_a_dismissal_is_an_event_not_an_edit(client):
    """This ledger is append-only. 'The user looked at it' is the same class of
    thing as the decision it dismisses."""
    client.post("/api/proposal", json={"items": OVER, "claimed_total_paise": 240_000})
    before = len(list(web.LEDGER.entries()))
    key = home(client)["decision"]["idempotency_key"]

    client.post("/api/home/seen", json={"idempotency_key": key})

    assert len(list(web.LEDGER.entries())) == before + 1
    web.LEDGER.verify()  # raises if anything earlier was touched


# --- the rule finally has a screen --------------------------------------------


def test_the_standing_rule_is_finally_served(client):
    """It is the central object of the product and had no route until now."""
    rule = home(client)["rule"]

    assert rule["per_txn_max_paise"] == 200_000
    assert rule["merchants"] == ["instamart"]
    # `fees` clears every policy by construction and is not a thing anyone chose
    # to buy, so it has no place in a sentence describing what you allowed.
    assert rule["categories"] == ["groceries"]
    assert rule["delivery"]["label"] == "Home"


# --- nothing a person reads looks like a symbol --------------------------------


def test_no_state_says_anything_that_reads_like_an_identifier(client):
    """The same rule the reason titles keep. A code reaching the screen is a bug
    in the wording table, and the user should not be the one who pays for it."""
    seen = []
    client.post("/api/proposal", json={"items": OVER, "claimed_total_paise": 240_000})
    out = home(client)
    seen += [out["headline"], out["detail"], out["chip"]]
    seen += [a["label"] for a in out["actions"]]
    seen += list(CHIPS.values()) + list(ACTIONS.values())

    for line in seen:
        assert "_" not in line, f"{line!r} reads like a symbol"
        assert not re.search(r"\b\w+\.\w+\b", line), f"{line!r} reads like a dotted path"


# --- every offered action has somewhere to go ---------------------------------
#
# They all used to open the chat thread, on the reasoning that the thread
# already renders carts and reasons. It does — but "View rule" that lands you in
# a conversation is not a view of the rule, and a button that does not do the
# thing written on it is worse than no button.


def test_every_action_the_engine_offers_has_a_route_behind_it(client):
    """The app maps each action id to a destination or an effect. This pins the
    server half: each one has something to call."""
    routes = {r.path for r in web.app.routes}
    behind = {
        "view_rule": "/api/home",
        "view_basket": "/api/home",
        "see_attempt": "/api/ledger",
        "verify_chain": "/api/ledger",
        "approve_once": "/api/mandate/one-time",
        "pause": "/api/list/{list_id}/schedule",
        "resume": "/api/list/{list_id}/schedule",
        "classify": "/api/list/{list_id}",
        "leave_out": "/api/list/{list_id}",
        "drop_flagged": "/api/list/{list_id}",
        "reauthorise": "/api/addresses",
        "cancel_basket": "/api/addresses",
        "pay": "/pay",
    }
    for action, route in behind.items():
        assert route in routes, f"{action} has nowhere to go: {route} is not a route"

    # Every id the wording table can emit is accounted for above, so a new
    # action cannot be added without deciding where it leads.
    from bounded_mandate.wording import ACTIONS

    undecided = set(ACTIONS) - set(behind) - {"not_now", "let_lapse"}
    assert not undecided, f"actions with no destination decided: {undecided}"


def test_a_list_edit_carries_the_users_own_categories(client):
    """What `classify` does: the user's classification, which is the only one
    the engine will take."""
    out = client.put(
        "/api/list/breakfast",
        json={
            "item_names": ["Toned milk 1L x2", "Eggs (12)", "Brown bread"],
            "categories": {"Eggs (12)": "groceries"},
        },
    )

    assert out.status_code == 200
    assert web.LISTS["breakfast"].category_of("Eggs (12)") == "groceries"


def test_the_cap_meter_measures_the_list_against_the_cap(client):
    """The bar under each list. It shipped unlabelled, which made it decoration
    — the first question anybody asked was what it measured."""
    row = client.get("/api/list/usual").json()

    assert row["total_paise"] == 185_000
    assert row["cap_paise"] == 200_000
    # 92.5% of the cap, which is what the bar is that full of, and what
    # "₹150 under your cap" says out loud.
    assert row["cap_paise"] - row["total_paise"] == 15_000
