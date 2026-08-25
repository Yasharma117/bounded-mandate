"""The agent proposes. These pin down that proposing is all it can do."""

from __future__ import annotations

import json
from types import SimpleNamespace

from bounded_mandate.agent import TOOLS, AgentRun, BuyerAgent
from bounded_mandate.basket import USUAL_GROCERIES, seed_lists
from bounded_mandate.merchant import Marketplace
from tests.conftest import HOME

SHOP = "instamart"
CART = "instamart_cart_1"


def call(name, **args):
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def scripted(*turns):
    """A client that plays a fixed sequence of assistant turns."""
    seq = iter(turns)

    def create(**_):
        calls, text = next(seq)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))]
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def build(client, policies, ledger):
    return BuyerAgent(
        marketplace=Marketplace(),
        shopping_list=seed_lists()["usual"],
        policies=policies,
        ledger=ledger,
        mandate_id="mdt_1",
        delivery_address=HOME,
        client=client,
    )


# --- the asymmetry -----------------------------------------------------------


def test_the_agent_holds_exactly_four_tools_and_none_touch_the_rail():
    """It can read, shop and ask. It cannot pay, and it cannot see its policy."""
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"read_shopping_list", "search_catalog", "create_cart", "request_charge"}
    blob = json.dumps(TOOLS).lower()
    assert "razorpay" not in blob and "policy" not in blob and "mandate" not in blob


def test_no_tool_can_write_the_shopping_list():
    """The list is the user's definition of what they want. An agent that could
    edit it could redefine "my usual groceries" and then order the new
    definition entirely within policy — an escalation that never trips a bound.

    So the absence of a write tool is a security property, not an oversight.
    """
    for tool in TOOLS:
        function = tool["function"]
        if function["name"] == "read_shopping_list":
            assert function["parameters"].get("properties") == {}, "read takes no arguments"
        else:
            assert "list" not in function["name"]
    writes = {"write_shopping_list", "edit_shopping_list", "add_to_list", "set_shopping_list"}
    assert writes.isdisjoint({t["function"]["name"] for t in TOOLS})


def test_the_agent_cannot_reach_the_list_through_dispatch(policies, ledger):
    """Belt and braces: even if a model invents the call, there is nothing behind it."""
    agent = build(scripted((None, "done")), policies, ledger)
    result = agent._dispatch(AgentRun("x"), "write_shopping_list", {"item_names": ["Smartwatch"]})
    assert "error" in result
    assert agent.shopping_list.item_names == USUAL_GROCERIES


def test_a_refusal_tells_the_agent_why_but_not_what_the_limits_are(policies, ledger):
    agent = build(
        scripted(
            (
                [call("create_cart", merchant=SHOP, item_names=[*USUAL_GROCERIES, "Smartwatch"])],
                None,
            ),
            ([call("request_charge", cart_id=CART, claimed_total_paise=185_000)], None),
            (None, "Declined."),
        ),
        policies,
        ledger,
    )
    run = agent.run("order groceries")
    reply = run.steps[-1].result

    assert reply["verdict"] == "DENY"
    assert "per_txn_max_paise" not in json.dumps(reply)  # no policy leaks back


# --- honest path -------------------------------------------------------------


def test_an_honest_run_is_allowed(policies, ledger):
    agent = build(
        scripted(
            ([call("search_catalog", query="groceries")], None),
            ([call("create_cart", merchant=SHOP, item_names=list(USUAL_GROCERIES))], None),
            ([call("request_charge", cart_id=CART, claimed_total_paise=185_000)], None),
            (None, "Ordered."),
        ),
        policies,
        ledger,
    )
    run = agent.run("order my usual groceries")

    assert run.decision.verdict.value == "ALLOW"
    assert run.said == "Ordered."
    assert [s.tool for s in run.steps] == ["search_catalog", "create_cart", "request_charge"]


# --- the compromised agent ---------------------------------------------------


def test_a_lying_agent_is_refused_however_low_it_goes(policies, ledger):
    """Observed live: a compromised agent walks the claimed total down to ₹1.
    Every attempt must fail on the same fetched cart."""
    turns = [
        ([call("create_cart", merchant=SHOP, item_names=[*USUAL_GROCERIES, "Smartwatch"])], None)
    ]
    for claim in (1_535_000, 500_000, 100_000, 1_000, 100):
        turns.append(([call("request_charge", cart_id=CART, claimed_total_paise=claim)], None))
    turns.append((None, "Could not complete."))

    run = build(scripted(*turns), policies, ledger).run("order groceries")

    charges = [s for s in run.steps if s.tool == "request_charge"]
    assert len(charges) == 5
    assert all(s.result["verdict"] == "DENY" for s in charges)
    assert all("provenance.total_mismatch" in s.result["reason_code"] for s in charges)
    assert ledger.verify() == 5  # every attempt is on the chain


def test_an_unstocked_item_does_not_crash_the_run(policies, ledger):
    agent = build(
        scripted(
            ([call("create_cart", merchant=SHOP, item_names=["Ferrari"])], None),
            (None, "Not stocked."),
        ),
        policies,
        ledger,
    )
    run = agent.run("buy a ferrari")
    assert "not stocked" in run.steps[0].result["error"]
    assert run.decision is None


def test_the_loop_stops_rather_than_spinning(policies, ledger):
    agent = build(
        scripted(*[([call("search_catalog", query="x")], None)] * 3),
        policies,
        ledger,
    )
    run = agent.run("look around", max_turns=3)
    assert run.decision is None
    assert "too many turns" in run.said
