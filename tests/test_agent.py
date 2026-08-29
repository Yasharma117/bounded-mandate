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


# --- the conversation ---------------------------------------------------------


def test_the_agent_is_reminded_of_what_was_said(policies, ledger):
    """Without this every turn arrived with no idea what the last one was, so
    "make it Blinkit instead" landed as a sentence about nothing."""
    seen: list[list[dict]] = []

    def create(**kwargs):
        seen.append(list(kwargs["messages"]))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    build(client, policies, ledger).run(
        "order it",
        history=[
            {"from": "user", "text": "what's on my list?"},
            {"from": "agent", "text": "Atta, rice and milk."},
        ],
    )

    roles = [m["role"] for m in seen[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert seen[0][2]["content"] == "Atta, rice and milk."
    assert seen[0][-1]["content"] == "order it"


def test_only_what_was_said_travels(policies, ledger):
    """A cart id from three turns ago is not context — it is a reference the
    agent could charge against long after the basket stopped existing, and the
    engine would then refuse a cart nobody meant to propose. Only `text` is
    forwarded, whatever else a caller puts in a turn."""
    seen: list[list[dict]] = []

    def create(**kwargs):
        seen.append(list(kwargs["messages"]))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    build(client, policies, ledger).run(
        "charge it",
        history=[{"from": "agent", "text": "Ready.", "cart_id": CART, "total_paise": 999}],
    )

    carried = json.dumps(seen[0])
    assert CART not in carried
    assert "999" not in carried
    assert "Ready." in carried


def test_a_long_conversation_cannot_push_out_the_system_prompt(policies, ledger):
    seen: list[list[dict]] = []

    def create(**kwargs):
        seen.append(list(kwargs["messages"]))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    history = [{"from": "user", "text": f"turn {i}"} for i in range(200)]
    build(client, policies, ledger).run("now order it", history=history)

    assert seen[0][0]["role"] == "system"
    assert len(seen[0]) <= 2 + 10, "history is not bounded"


# --- a model producing junk is expected -----------------------------------------


def test_a_malformed_money_argument_is_an_error_not_a_crash(policies, ledger):
    """Seen live: the model emitted `'18>\\n185000'` for the total and an
    unguarded `int()` took the whole run out as a 502 mid-conversation. A bad
    argument should cost one retry, the same as `not stocked`."""
    agent = build(scripted(), policies, ledger)
    out = agent._dispatch(
        AgentRun("x"), "request_charge", {"cart_id": CART, "claimed_total_paise": "18>\n185000"}
    )

    assert "error" in out
    assert "paise" in out["error"]


def test_a_money_argument_is_never_guessed_at(policies, ledger):
    """This figure is the claim the whole provenance check compares against, so
    a value we had to interpret is worse than no value."""
    from bounded_mandate.agent import _paise

    assert _paise(185_000) == 185_000
    assert _paise("185000") == 185_000
    for junk in (18.5, True, "1_850", "₹1850", "abc", None, {}, []):
        assert _paise(junk) is None, f"{junk!r} was coerced"


def test_a_malformed_item_list_is_an_error_not_a_crash(policies, ledger):
    agent = build(scripted(), policies, ledger)
    out = agent._dispatch(AgentRun("x"), "create_cart", {"item_names": "Brown bread"})

    assert "error" in out
