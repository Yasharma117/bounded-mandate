"""The agent proposes. These pin down that proposing is all it can do."""

from __future__ import annotations

import json
from types import SimpleNamespace

from bounded_mandate.agent import TOOLS, BuyerAgent
from bounded_mandate.merchant import USUAL_GROCERIES, MockMerchant
from tests.conftest import HOME


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
        merchant=MockMerchant(),
        policies=policies,
        ledger=ledger,
        mandate_id="mdt_1",
        delivery_address=HOME,
        client=client,
    )


# --- the asymmetry -----------------------------------------------------------


def test_the_agent_holds_exactly_three_tools_and_none_touch_the_rail():
    """It can shop and it can ask. It cannot pay, and it cannot see its policy."""
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"search_catalog", "create_cart", "request_charge"}
    blob = json.dumps(TOOLS).lower()
    assert "razorpay" not in blob and "policy" not in blob and "mandate" not in blob


def test_a_refusal_tells_the_agent_why_but_not_what_the_limits_are(policies, ledger):
    agent = build(
        scripted(
            ([call("create_cart", item_names=[*USUAL_GROCERIES, "Smartwatch"])], None),
            ([call("request_charge", cart_id="cart_1", claimed_total_paise=185_000)], None),
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
            ([call("create_cart", item_names=list(USUAL_GROCERIES))], None),
            ([call("request_charge", cart_id="cart_1", claimed_total_paise=185_000)], None),
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
    turns = [([call("create_cart", item_names=[*USUAL_GROCERIES, "Smartwatch"])], None)]
    for claim in (1_535_000, 500_000, 100_000, 1_000, 100):
        turns.append(([call("request_charge", cart_id="cart_1", claimed_total_paise=claim)], None))
    turns.append((None, "Could not complete."))

    run = build(scripted(*turns), policies, ledger).run("order groceries")

    charges = [s for s in run.steps if s.tool == "request_charge"]
    assert len(charges) == 5
    assert all(s.result["verdict"] == "DENY" for s in charges)
    assert all("provenance.total_mismatch" in s.result["reason_code"] for s in charges)
    assert ledger.verify() == 5  # every attempt is on the chain


def test_an_unstocked_item_does_not_crash_the_run(policies, ledger):
    agent = build(
        scripted(([call("create_cart", item_names=["Ferrari"])], None), (None, "Not stocked.")),
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
