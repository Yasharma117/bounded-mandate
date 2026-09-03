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


def call(tool, **args):
    """`tool` rather than `name` because `propose_list` takes a `name` argument
    of its own, and the two collided."""
    return SimpleNamespace(
        id=f"call_{tool}",
        function=SimpleNamespace(name=tool, arguments=json.dumps(args)),
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


def test_the_agent_holds_five_tools_and_none_touch_the_rail():
    """It can read, draft, shop and ask. It cannot pay, cannot see its policy,
    and cannot store anything — `propose_list` writes a list *out*, and only
    the account holder can turn that into one."""
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {
        "read_shopping_list",
        "propose_list",
        "search_catalog",
        "create_cart",
        "request_charge",
    }
    blob = json.dumps(TOOLS).lower()
    assert "razorpay" not in blob and "policy" not in blob and "mandate" not in blob


def test_no_tool_can_write_the_shopping_list():
    """The list is the user's definition of what they want. An agent that could
    edit it could redefine "my usual groceries" and then order the new
    definition entirely within policy — an escalation that never trips a bound.

    So the absence of a write tool is a security property, not an oversight.
    """
    read = next(t["function"] for t in TOOLS if t["function"]["name"] == "read_shopping_list")
    assert read["parameters"].get("properties") == {}, "read takes no arguments"

    writes = {"write_shopping_list", "edit_shopping_list", "add_to_list", "set_shopping_list"}
    assert writes.isdisjoint({t["function"]["name"] for t in TOOLS})

    # `propose_list` writes one *out*, for the account holder to approve. That
    # is a proposal, not a write — the same shape as `request_charge`, which
    # asks the engine rather than moving money. The behavioural check is in
    # `test_drafting_a_list_changes_nothing` below; naming alone proves little.


def test_drafting_a_list_changes_nothing(policies, ledger):
    """What `propose_list` may do, stated as what it leaves behind: nothing."""
    agent = build(scripted((None, "done")), policies, ledger)
    before = agent.shopping_list.item_names
    run = AgentRun("x")

    out = agent._dispatch(
        run, "propose_list", {"name": "Snacks", "item_names": ["Blue Lays x3"], "every_days": 7}
    )

    assert out["drafted"] is True
    assert run.draft.item_names == ("Blue Lays x3",)
    assert run.draft.every_days == 7
    # The user's own list is untouched, which is the whole point.
    assert agent.shopping_list.item_names == before


def test_a_draft_refuses_to_be_empty(policies, ledger):
    agent = build(scripted((None, "done")), policies, ledger)
    out = agent._dispatch(AgentRun("x"), "propose_list", {"name": "Snacks", "item_names": []})
    assert "error" in out


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
                [
                    call(
                        "create_cart",
                        asked_for="once",
                        merchant=SHOP,
                        item_names=[*USUAL_GROCERIES, "Smartwatch"],
                    )
                ],
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
            (
                [
                    call(
                        "create_cart",
                        asked_for="once",
                        merchant=SHOP,
                        item_names=list(USUAL_GROCERIES),
                    )
                ],
                None,
            ),
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
        (
            [
                call(
                    "create_cart",
                    asked_for="once",
                    merchant=SHOP,
                    item_names=[*USUAL_GROCERIES, "Smartwatch"],
                )
            ],
            None,
        )
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
            ([call("create_cart", asked_for="once", merchant=SHOP, item_names=["Ferrari"])], None),
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


def test_a_repeating_draft_needs_a_real_cadence(policies, ledger):
    """`every_days` is required now, so the ways it can arrive wrong are the
    thing to pin. A bool is not a cadence — `int(True)` is 1, which would turn
    `every_days: true` into a daily order — and 400 days is one `POST /api/lists`
    will refuse, so offering it drafts a card that cannot be saved."""
    agent = build(scripted(), policies, ledger)
    for bad in (None, 0, -7, True, 3.9, "soon", 400):
        run = AgentRun("x")
        out = agent._dispatch(
            run,
            "propose_list",
            {"name": "Snacks", "item_names": ["Blue Lays x3"], "every_days": bad},
        )
        assert "error" in out, f"{bad!r} was accepted as a cadence"
        assert run.draft is None

    run = AgentRun("x")
    agent._dispatch(
        run, "propose_list", {"name": "Snacks", "item_names": ["Blue Lays x3"], "every_days": 7}
    )
    assert run.draft is not None and run.draft.every_days == 7


def test_a_repeating_cart_is_sent_back_to_the_drafting_tool(policies, ledger):
    """`asked_for="repeating"` used to build a cart and charge for it, which is
    the guess the whole cadence gate exists to stop: a standing order placed once
    and never again, or charged now for something they wanted weekly."""
    agent = build(scripted(), policies, ledger)
    out = agent._dispatch(
        AgentRun("x"),
        "create_cart",
        {"item_names": ["Toned milk 1L x2"], "asked_for": "repeating", "merchant": SHOP},
    )
    assert "error" in out and "cart_id" not in out
    assert "propose_list" in out.get("do_this", "")

    # And a value from neither the enum nor the schema is refused rather than
    # falling through to a purchase.
    junk = agent._dispatch(
        AgentRun("x"),
        "create_cart",
        {"item_names": ["Toned milk 1L x2"], "asked_for": "sure", "merchant": SHOP},
    )
    assert "error" in junk and "cart_id" not in junk


def test_the_agent_is_told_a_refusal_is_answered_somewhere_it_cannot_reach(policies, ledger):
    """The loop this closes, seen live: an item outside the grocery scope was
    escalated, the user approved and paid it on the card, and every following
    turn rebuilt the same basket and read the same refusal back to them.

    Two things were missing. The conversation never recorded the approval — the
    client fixes that by saying it into the thread — and the agent had no rule
    for the sentence that comes after a refusal. "Yes, go ahead" is not
    permission it can act on, because approving one basket is a button only the
    account holder can press, and there is no tool here that reaches it.
    """
    seen: list[list[dict]] = []

    def create(**kwargs):
        seen.append(list(kwargs["messages"]))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    build(client, policies, ledger).run(
        "yes, go ahead",
        history=[
            {"from": "user", "text": "order a bar of chocolate"},
            {"from": "agent", "text": "That needs you — chocolate is outside your scope."},
        ],
    )

    prompt = seen[0][0]["content"]
    assert seen[0][0]["role"] == "system"
    # It cannot approve, and it is told where approving actually happens.
    assert "approve" in prompt.lower()
    assert "CALL NO TOOLS" in prompt
    # And a basket this conversation already paid for is not one to order again.
    assert "paid" in prompt.lower()

    # No tool mints authority, which is what makes the rule above safe to state
    # as manners rather than enforce: the worst a disobedient turn costs is a
    # second refusal, never a second charge.
    assert not {"approve", "grant", "one_time"} & {t["function"]["name"] for t in TOOLS}


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


def test_no_tool_can_set_the_rule_it_is_judged_against():
    """The mirror of `test_no_tool_writes_the_shopping_list`, and the same
    reasoning. An agent that could edit its own list could redefine "my usual
    groceries" and then order the new definition within policy; an agent that
    could edit its own *mandate* would not even need the disguise.

    `PUT /api/mandate` is the route where authority is created, and it is
    reachable only by the account holder — there is no tool for it, and there is
    no tool that takes a cap, a merchant list or a cadence as an argument at all.
    """
    names = {t["function"]["name"] for t in TOOLS}
    assert names.isdisjoint({"set_rule", "set_mandate", "edit_policy", "update_mandate"})

    # Not by name alone — nothing may take a *mandate* bound as a parameter
    # under any name.
    #
    # `every_days` is deliberately not in this set, and the distinction is the
    # interesting part. `propose_list` carries one, but that is a list's
    # schedule, and a schedule cannot widen authority: the engine never reads
    # one, so a list set to run hourly under a mandate permitting one order
    # every four days is simply refused three times a day. The mandate's own
    # window is `window_days`, which no tool touches.
    bounds = {
        "per_txn_max_paise",
        "max_amount_paise",
        "cap",
        "cap_paise",
        "merchants",
        "window_days",
        "cadence_days",
    }
    for tool in TOOLS:
        params = set(tool["function"]["parameters"].get("properties", {}))
        assert params.isdisjoint(bounds), (
            f"{tool['function']['name']} takes a bound: {params & bounds}"
        )


# --- once, or every time -----------------------------------------------------


def test_the_agent_is_told_to_ask_rather_than_guess_the_cadence():
    """A one-off and a standing order are different actions, and neither is a
    milder version of the other. Guessing repeating leaves somebody with an
    order arriving every week they never asked for.

    This pins the instruction, not the behaviour — a prompt line is only worth
    what the model does with it, and `test_live` is where that is measured.
    """
    from bounded_mandate.agent import SYSTEM, TOOLS

    assert "once, or every time" in SYSTEM
    assert "CALL NO TOOLS while you wait" in SYSTEM

    # The tool the model would reach for by mistake says the same thing, since
    # a description is read at the moment of choosing and the system prompt was
    # read a long way back.
    drafting = next(t["function"] for t in TOOLS if t["function"]["name"] == "propose_list")
    assert "ask them in a sentence instead of calling this" in drafting["description"]


def test_asking_which_one_spends_nothing(policies, ledger):
    """The whole point of asking is that nothing has happened yet."""
    agent = build(
        scripted(([], "Just this once, or should I set that up to repeat?")), policies, ledger
    )

    out = agent.run("order milk and bread")

    assert out.steps == []
    assert out.decision is None
    assert out.draft is None
    assert "repeat" in out.said


def test_a_repeating_order_is_drafted_and_not_charged(policies, ledger):
    """Approving the list is what starts it. A draft that also charged would
    have taken the first order without being asked for it."""
    agent = build(
        scripted(
            (
                [
                    call(
                        "propose_list",
                        name="Weekly milk",
                        item_names=["Toned milk 1L x2"],
                        every_days=7,
                    )
                ],
                None,
            ),
            ([], "Drafted — approve it and it will go out every week."),
        ),
        policies,
        ledger,
    )

    out = agent.run("get me milk every week")

    assert out.draft is not None
    assert out.draft.every_days == 7
    assert out.decision is None, "a draft must not charge"
    assert [s.tool for s in out.steps] == ["propose_list"]
