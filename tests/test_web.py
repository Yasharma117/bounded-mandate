"""The HTTP surface. What matters most is what it refuses."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from bounded_mandate import web
from bounded_mandate.basket import USUAL_GROCERIES
from bounded_mandate.engine import Proposal, Verdict, decide
from bounded_mandate.razorpay_gateway import GatewayAuthError, GatewayError, SignatureMismatch
from tests.conftest import FakeGateway  # noqa: F401  — used by tests below

USUAL = list(USUAL_GROCERIES)


def propose(client, items, claimed):
    return client.post(
        "/api/proposal", json={"items": items, "claimed_total_paise": claimed}
    ).json()


# --- the engine gates the rail ------------------------------------------------


def test_an_allowed_proposal_reaches_razorpay(client):
    out = propose(client, USUAL, 185_000)

    assert out["verdict"] == "ALLOW"
    assert out["order_id"] == "order_charge_1"
    assert client.gateway.charged == [185_000]  # the fetched total, not the claim


def test_a_lying_agent_never_reaches_razorpay(client):
    """The hero, at the HTTP boundary: denied, and no order is created."""
    out = propose(client, [*USUAL, "Smartwatch"], 185_000)

    assert out["verdict"] == "DENY"
    assert "provenance.total_mismatch" in out["reason_code"]
    assert out["order_id"] is None
    assert client.gateway.charged == []


def test_an_escalation_never_reaches_razorpay(client):
    out = propose(client, [*USUAL, "Bluetooth earbuds", "Phone case"], 240_000)

    assert out["verdict"] == "ESCALATE"
    assert out["order_id"] is None
    assert client.gateway.charged == []


def test_the_response_shows_both_totals_so_the_lie_is_visible(client):
    out = propose(client, [*USUAL, "Smartwatch"], 185_000)
    assert out["claimed_total_paise"] == 185_000
    assert out["real_total_paise"] == 1_685_000


def test_an_unstocked_item_is_a_400(client):
    assert (
        client.post(
            "/api/proposal", json={"items": ["Ferrari"], "claimed_total_paise": 100}
        ).status_code
        == 400
    )


# --- settlement ---------------------------------------------------------------


def test_a_verified_settlement_is_written_to_the_ledger(client):
    propose(client, USUAL, 185_000)
    client.post("/api/settlement/verify", json=CALLBACK)

    entries = client.get("/api/ledger").json()
    assert entries["chain_intact"]
    assert entries["entries"][-1]["razorpay_payment_id"] == "pay_1"


def test_a_forged_settlement_writes_nothing(client):
    propose(client, USUAL, 185_000)
    before = len(client.get("/api/ledger").json()["entries"])
    client.gateway.verify_error = SignatureMismatch("nope")

    assert client.post("/api/settlement/verify", json=CALLBACK).status_code == 400
    assert len(client.get("/api/ledger").json()["entries"]) == before


def test_the_page_loads_and_pulls_in_razorpay_checkout(client):
    body = client.get("/").text
    assert "checkout.razorpay.com/v1/checkout.js" in body


def test_the_page_never_carries_the_secret(client):
    """The one thing that must never be served."""
    assert "RAZORPAY_KEY_SECRET" not in client.get("/").text
    assert "key_secret" not in client.get("/").text.lower()


def test_creating_a_mandate_order_returns_only_public_fields(client):
    body = client.post("/api/mandate/order", json={"max_amount_paise": 200_000}).json()
    assert body == {
        "order_id": "order_1",
        "customer_id": "cust_1",
        "amount": 100,
        "currency": "INR",
        "key_id": "rzp_test_key",
    }


def test_a_cap_razorpay_would_reject_is_a_400(client):
    assert client.post("/api/mandate/order", json={"max_amount_paise": 99}).status_code == 400


def test_a_missing_cap_is_a_422(client):
    assert client.post("/api/mandate/order", json={}).status_code == 422


def test_bad_credentials_are_a_401_not_a_500(client):
    client.gateway.order_error = GatewayAuthError("Authentication failed")
    assert client.post("/api/mandate/order", json={"max_amount_paise": 200_000}).status_code == 401


def test_an_upstream_failure_is_a_500(client):
    client.gateway.order_error = GatewayError("upstream on fire")
    assert client.post("/api/mandate/order", json={"max_amount_paise": 200_000}).status_code == 500


CALLBACK = {
    "razorpay_order_id": "order_1",
    "razorpay_payment_id": "pay_1",
    "razorpay_signature": "sig",
}


def test_a_verified_callback_returns_the_mandate_token(client):
    body = client.post("/api/mandate/verify", json=CALLBACK).json()
    assert body == {"verified": True, "token_id": "token_abc", "payment_id": "pay_1"}


def test_a_forged_callback_is_a_400_and_registers_nothing(client):
    client.gateway.verify_error = SignatureMismatch("nope")
    response = client.post("/api/mandate/verify", json=CALLBACK)

    assert response.status_code == 400
    assert "verified" not in response.json()


@pytest.mark.parametrize("missing", list(CALLBACK))
def test_a_callback_missing_any_field_is_refused(client, missing):
    partial = {k: v for k, v in CALLBACK.items() if k != missing}
    assert client.post("/api/mandate/verify", json=partial).status_code == 422


def test_no_route_moves_money_without_an_engine_verdict(client):
    """Nothing here accepts \"charge this\" as an instruction. The rail is reached
    only as a consequence of a proposal the engine allowed.

    `/pay` is not a counterexample: it is a *page*, it takes no body, and the
    order it renders was created by `_settle` under an ALLOW. It cannot mint one
    — the test below spends the whole grant flow proving that.
    """
    paths = {r.path for r in web.app.routes}
    assert not {p for p in paths if "charge" in p}

    before = list(client.gateway.charged)
    assert client.get("/pay?grant=grant_nope").status_code == 200
    assert client.post("/pay").status_code == 405
    assert client.gateway.charged == before, "rendering the checkout charged something"


def test_webhooks_are_refused_until_a_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    assert client.post("/api/webhook/razorpay", json={}).status_code == 503


# --- the agent route -------------------------------------------------------


class FakeAgent:
    """Stands in for the model. The engine it calls is the real one."""

    def __init__(self, run_result=None, blow_up=False, **kwargs):
        self.kwargs = kwargs
        self.run_result, self.blow_up = run_result, blow_up

    def run(self, instruction, **_):
        if self.blow_up:
            raise RuntimeError("model unreachable")
        return self.run_result


def _agent_run(verdict, *, claimed=185_000, said="Ordered."):
    from bounded_mandate.agent import AgentRun, Step
    from bounded_mandate.engine import Decision, Reason

    run = AgentRun(instruction="order the usual")
    run.said = said
    run.steps = [
        Step("create_cart", {"item_names": ["Milk"]}, {"cart_id": "c1", "item_count": 12}),
        Step("request_charge", {"cart_id": "c1", "claimed_total_paise": claimed}, {}),
    ]
    run.decision = Decision(
        verdict=verdict,
        reasons=() if verdict is Verdict.ALLOW else (Reason("cap.exceeded", verdict, "Too much."),),
        mandate_id="mdt_demo",
        cart_id="c1",
        total_paise=185_000,
        idempotency_key="k" * 32,
    )
    return run


def test_the_agent_route_reports_what_the_agent_did_and_what_the_engine_ruled(client, monkeypatch):
    monkeypatch.setattr(
        web, "BuyerAgent", lambda **kw: FakeAgent(run_result=_agent_run(Verdict.ALLOW), **kw)
    )
    body = client.post("/api/agent", json={"text": "order the usual"}).json()
    assert body["said"] == "Ordered."
    assert [s["tool"] for s in body["steps"]] == ["create_cart", "request_charge"]
    assert body["decision"]["verdict"] == "ALLOW"
    assert body["decision"]["order_id"] == "order_charge_1"


def test_a_refused_agent_run_creates_no_order(client, monkeypatch):
    monkeypatch.setattr(
        web, "BuyerAgent", lambda **kw: FakeAgent(run_result=_agent_run(Verdict.DENY), **kw)
    )
    decision = client.post("/api/agent", json={"text": "order the usual"}).json()["decision"]
    assert decision["verdict"] == "DENY"
    assert decision["order_id"] is None
    assert client.gateway.charged == []


def test_the_adversarial_flag_changes_the_agent_not_the_engine(client, monkeypatch):
    seen = {}

    def capture(**kw):
        seen.update(kw)
        return FakeAgent(run_result=_agent_run(Verdict.DENY), **kw)

    monkeypatch.setattr(web, "BuyerAgent", capture)
    client.post("/api/agent", json={"text": "order the usual", "adversarial": True})
    assert seen["system"] is not None
    # The policy handed to the agent is the same object the engine enforces.
    assert seen["policies"] is web.POLICIES


def test_a_model_outage_is_a_502_not_a_silent_approval(client, monkeypatch):
    monkeypatch.setattr(web, "BuyerAgent", lambda **kw: FakeAgent(blow_up=True, **kw))
    assert client.post("/api/agent", json={"text": "order the usual"}).status_code == 502


# --- the shopping list: the user's other source of truth --------------------


def test_the_list_comes_back_priced_against_the_allowed_merchant(client):
    body = client.get("/api/list/usual").json()
    assert body["merchant"] == "instamart"
    assert [i["name"] for i in body["items"]] == list(USUAL_GROCERIES)
    assert body["total_paise"] == 185_000
    assert body["cap_paise"] == 200_000
    assert body["unstocked"] == []
    assert all(i["url"].startswith("/m/instamart/p/") for i in body["items"])


def test_a_user_can_edit_their_list_and_it_reprices(client):
    keep = list(USUAL_GROCERIES)[:3]
    body = client.put("/api/list/usual", json={"item_names": keep}).json()
    assert [i["name"] for i in body["items"]] == keep
    assert body["total_paise"] == 53_000
    # And it persists — this is a source of truth, not a scratch value.
    assert client.get("/api/list/usual").json()["total_paise"] == 53_000


def test_the_list_refuses_items_no_shop_stocks(client):
    response = client.put("/api/list/usual", json={"item_names": ["Ferrari"]})
    assert response.status_code == 400
    assert client.get("/api/list/usual").json()["total_paise"] == 185_000


def test_an_unknown_list_is_a_404_both_ways(client):
    assert client.get("/api/list/nope").status_code == 404
    assert client.put("/api/list/nope", json={"item_names": []}).status_code == 404


def test_editing_the_list_never_authorises_anything(client):
    """The list says *what*. The policy says *how much*. Editing one must not
    touch the other, or every basket change would read as a spending change."""
    before = sum(1 for _ in web.LEDGER.entries())
    client.put("/api/list/usual", json={"item_names": [*USUAL_GROCERIES, "Smartwatch"]})
    assert sum(1 for _ in web.LEDGER.entries()) == before
    assert client.gateway.charged == []
    # The cap is untouched, so the bigger list simply escalates when proposed.
    assert web.POLICIES["mdt_demo"].per_txn_max_paise == 200_000


# --- cross-merchant prices --------------------------------------------------


def test_the_catalog_shows_every_shop_and_which_are_in_policy(client):
    offers = client.get("/api/catalog", params={"q": "Bananas 1kg"}).json()["offers"]
    by_merchant = {o["merchant"]: o for o in offers}
    assert set(by_merchant) == {"instamart", "blinkit", "zepto"}
    # The cheapest shop is deliberately the one the mandate does not cover.
    cheapest = min(offers, key=lambda o: o["price_paise"])
    assert cheapest["merchant"] == "blinkit"
    assert cheapest["merchant_allowed"] is False
    assert by_merchant["instamart"]["merchant_allowed"] is True
    assert all(o["category_allowed"] for o in offers)


def test_the_catalog_separates_a_disallowed_shop_from_a_disallowed_category(client):
    """An allowed shop selling a disallowed thing must not read as a disallowed
    shop. Naming the wrong reason is worse than naming none."""
    (watch,) = client.get("/api/catalog", params={"q": "Smartwatch"}).json()["offers"]
    assert watch["merchant"] == "instamart"
    assert watch["merchant_allowed"] is True
    assert watch["category_allowed"] is False


def test_a_product_link_actually_resolves(client):
    offer = client.get("/api/catalog", params={"q": "Bananas 1kg"}).json()["offers"][0]
    page = client.get(offer["url"])
    assert page.status_code == 200
    assert "Bananas 1kg" in page.text


def test_an_unstocked_product_page_is_a_404(client):
    assert client.get("/m/instamart/p/Ferrari").status_code == 404
    assert client.get("/m/nosuchshop/p/Bananas%201kg").status_code == 404


def test_the_agent_builds_from_the_edited_list_not_the_seeded_one(client, monkeypatch):
    """The list is a source of truth for the *agent*, not only for the screen.

    The user putting something off-scope on their own list is the interesting
    case: the agent obeys the list, and the engine still refuses — because the
    list says *what*, and the policy says *whether*.
    """
    client.put("/api/list/usual", json={"item_names": ["Toned milk 1L x2", "Smartwatch"]})

    seen: dict = {}

    class ListReadingAgent:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, _instruction, **_):
            from bounded_mandate.agent import AgentRun

            return AgentRun(instruction="x", said="done")

    monkeypatch.setattr(web, "BuyerAgent", ListReadingAgent)
    client.post("/api/agent", json={"text": "order my usual groceries"})

    handed = seen["shopping_list"]
    assert handed.item_names == ("Toned milk 1L x2", "Smartwatch")


def test_the_app_sees_policy_flags_the_agent_never_did(client, monkeypatch):
    """Offers reach the app annotated with the policy's judgement, and reach the
    *model* bare. Knowing why it was refused is not the same as being able to
    see what it is refused for, and a search result carrying the allowlist would
    hand it over."""

    class SearchingAgent:
        def __init__(self, **kwargs):
            self.marketplace = kwargs["marketplace"]

        def run(self, _instruction, **_):
            from bounded_mandate.agent import AgentRun, Step

            raw = {
                "offers": [
                    {
                        "merchant": "blinkit",
                        "name": "Bananas 1kg",
                        "price_paise": 5_400,
                        "category": "groceries",
                    },
                    {
                        "merchant": "instamart",
                        "name": "Bananas 1kg",
                        "price_paise": 6_000,
                        "category": "groceries",
                    },
                ]
            }
            # What the model was handed carries no verdict at all.
            assert all("merchant_allowed" not in o for o in raw["offers"])
            run = AgentRun(instruction="x", said="here are prices")
            run.steps = [Step("search_catalog", {"query": "Bananas 1kg"}, raw)]
            return run

    monkeypatch.setattr(web, "BuyerAgent", SearchingAgent)
    body = client.post("/api/agent", json={"text": "what do bananas cost"}).json()

    offers = body["steps"][0]["result"]["offers"]
    by_merchant = {o["merchant"]: o for o in offers}
    assert by_merchant["blinkit"]["merchant_allowed"] is False
    assert by_merchant["instamart"]["merchant_allowed"] is True
    assert all(o["url"].startswith("/m/") for o in offers)


def test_a_verdict_reaches_the_app_in_words_as_well_as_codes(client, monkeypatch):
    """`category.not_allowed+cap.exceeded` is right for the ledger and wrong for
    a person. Both forms travel; the app decides which to show."""
    monkeypatch.setattr(
        web, "BuyerAgent", lambda **kw: FakeAgent(run_result=_agent_run(Verdict.DENY), **kw)
    )
    decision = client.post("/api/agent", json={"text": "order"}).json()["decision"]

    assert decision["reason_code"] == "cap.exceeded"
    assert decision["summary"] == "Over your limit"
    assert decision["reasons"][0]["title"] == "Over your limit"
    # And nothing in what a user reads still looks like an identifier.
    assert "_" not in decision["summary"]
    assert all("_" not in r["title"] for r in decision["reasons"])


def test_the_card_says_what_happened_to_the_money_without_saying_rail(client, monkeypatch):
    monkeypatch.setattr(
        web, "BuyerAgent", lambda **kw: FakeAgent(run_result=_agent_run(Verdict.DENY), **kw)
    )
    refused = client.post("/api/agent", json={"text": "order"}).json()["decision"]
    assert refused["settlement"] == "Nothing was charged"

    monkeypatch.setattr(
        web, "BuyerAgent", lambda **kw: FakeAgent(run_result=_agent_run(Verdict.ALLOW), **kw)
    )
    allowed = client.post("/api/agent", json={"text": "order"}).json()["decision"]
    # Honest about the gap: an order exists, but nothing has been captured.
    assert allowed["settlement"] == "Order placed, not yet paid"
    assert "rail" not in allowed["settlement"].lower()


# --- many lists, and when they run ------------------------------------------


def test_the_user_keeps_several_lists_soonest_first(client):
    rows = client.get("/api/lists").json()["lists"]
    assert len(rows) >= 2
    due = [row["next_due_at"] for row in rows if row["next_due_at"]]
    assert due == sorted(due), "the one that runs next should be at the top"
    assert all(row["schedule"] for row in rows), "every list says when it runs"


def test_a_one_time_list_can_be_created_and_reads_as_one_off(client):
    body = client.post(
        "/api/lists",
        json={
            "name": "Diwali sweets",
            "item_names": ["Cow ghee 500ml", "Bananas 1kg"],
            "kind": "once",
            "run_on": "2026-11-08",
        },
    ).json()
    assert body["kind"] == "once"
    assert body["schedule"] == "Once, on 8 Nov"
    assert body["total_paise"] == 40_000
    # And it joins the others rather than replacing anything.
    assert len(client.get("/api/lists").json()["lists"]) >= 3


def test_creating_a_list_refuses_items_no_shop_stocks(client):
    response = client.post("/api/lists", json={"name": "Dream", "item_names": ["Ferrari"]})
    assert response.status_code == 400


def test_two_lists_with_the_same_name_get_their_own_ids(client):
    first = client.post("/api/lists", json={"name": "Party"}).json()
    second = client.post("/api/lists", json={"name": "Party"}).json()
    assert first["list_id"] != second["list_id"]


def test_a_schedule_can_be_changed_without_restating_the_list(client):
    before = client.get("/api/list/usual").json()
    after = client.put("/api/list/usual/schedule", json={"every_days": 7}).json()
    assert after["every_days"] == 7
    assert after["schedule"] == "Every 7 days"
    assert [i["name"] for i in after["items"]] == [i["name"] for i in before["items"]]


def test_pausing_a_list_stops_it_being_due_without_deleting_it(client):
    paused = client.put("/api/list/usual/schedule", json={"paused": True}).json()
    assert paused["paused"] is True
    assert paused["next_due_at"] is None
    assert paused["schedule"] == "Paused"
    assert paused["items"], "a paused list keeps its contents"


def test_a_list_can_be_deleted(client):
    assert client.delete("/api/list/breakfast").status_code == 200
    assert client.get("/api/list/breakfast").status_code == 404
    assert client.delete("/api/list/breakfast").status_code == 404


# --- the scheduler ----------------------------------------------------------


def test_a_due_list_goes_out_on_its_own(client):
    """The product's claim is that nobody is present. This is that, once."""
    ran = client.post("/api/lists/run-due").json()["ran"]
    assert ran, "nothing fired, though the seeded lists have never run"
    assert all("verdict" in row for row in ran)
    # And it does not fire the same basket again on the next tick.
    assert client.post("/api/lists/run-due").json()["ran"] == []


def test_the_scheduler_cannot_widen_what_the_engine_permits(client):
    """A list set to run every day under a mandate permitting one order every
    four days is refused, daily. The scheduler proposes; it never decides."""
    client.put("/api/list/breakfast/schedule", json={"paused": True})
    client.put("/api/list/usual/schedule", json={"every_days": 0})

    verdicts = []
    for _ in range(3):
        for row in client.post("/api/lists/run-due").json()["ran"]:
            verdicts.append(row["verdict"])
        # Pretend a tick passed by clearing the last run.
        web.LISTS["usual"] = replace(web.LISTS["usual"], last_run_at=None)

    assert verdicts, "the list should have been attempted"
    assert verdicts[0] == "ALLOW"
    assert any(v != "ALLOW" for v in verdicts[1:]), "the cap should have stopped it"


def test_the_scheduler_is_off_unless_it_is_switched_on(monkeypatch):
    """A background task that runs an agent has no business starting itself
    inside a test suite, or on someone's laptop by surprise."""
    monkeypatch.delenv("BM_SCHEDULER", raising=False)
    with TestClient(web.app) as running:
        running.get("/api/lists")
        assert not hasattr(running.app.state, "scheduler")


def test_a_list_line_carries_the_users_own_category(client):
    body = client.get("/api/list/usual").json()
    assert all(i["category"] == "groceries" for i in body["items"])


def test_editing_a_list_keeps_the_categories_of_lines_it_kept(client):
    """Reclassifying every line on every edit would be busywork the user did
    not ask for, and forgetting one turns a silent run into an escalation."""
    keep = list(USUAL_GROCERIES)[:3]
    body = client.put("/api/list/usual", json={"item_names": keep}).json()
    assert all(i["category"] == "groceries" for i in body["items"])


def test_a_user_can_reclassify_a_line(client):
    """Their authority, the same as editing their own cap — and written down
    where they can read it."""
    body = client.put(
        "/api/list/usual",
        json={
            "item_names": ["Cow ghee 500ml"],
            "categories": {"Cow ghee 500ml": "household"},
        },
    ).json()
    assert body["items"][0]["category"] == "household"


# --- where things get delivered ----------------------------------------------


def test_the_address_book_says_which_one_orders_go_to(client):
    out = client.get("/api/addresses").json()

    assert [a["label"] for a in out["addresses"]] == ["Home", "Office"]
    selected = [a for a in out["addresses"] if a["selected"]]
    assert len(selected) == 1
    assert selected[0]["address_id"] == out["delivery_id"] == "home"
    # Selected and authorised are two different questions, answered separately.
    assert selected[0]["authorised"]


def test_choosing_an_address_moves_both_the_cart_and_the_policy(client):
    client.put("/api/address", json={"address_id": "office"})

    assert web.POLICIES["mdt_demo"].delivery_addresses == frozenset({"office"})
    out = propose(client, USUAL, 185_000)
    assert out["delivery_id"] == "office"
    # And it still clears — the point is that the policy moved with it, not that
    # the check was skipped.
    assert out["verdict"] == "ALLOW"


def test_the_previous_address_stops_being_authorised(client):
    """Replaced, not accumulated. A mandate should authorise where you actually
    deliver; addresses that pile up are authority nobody remembers granting."""
    client.put("/api/address", json={"address_id": "office"})

    stale = web.MARKETPLACE.create_cart(USUAL, delivery_address="home", merchant="instamart")
    decision = decide(
        Proposal("mdt_demo", stale.cart_id, stale.total_paise),
        policies=web.POLICIES,
        adapter=web.MARKETPLACE,
        ledger=web.LEDGER,
    )

    assert "delivery.unknown_address" in decision.reason_code


def test_an_address_not_on_the_account_is_refused(client):
    """The one thing selection must not become: a way to introduce a doorstep."""
    before = web.POLICIES["mdt_demo"].delivery_addresses

    response = client.put("/api/address", json={"address_id": "9 Somebody Elses Lane"})

    assert response.status_code == 404
    assert web.POLICIES["mdt_demo"].delivery_addresses == before


def test_the_agent_holds_no_tool_that_moves_the_address(client):
    """`_create_cart` takes items and a merchant. There is no third argument,
    which is why an injected prompt has nowhere to put an address."""
    import inspect

    from bounded_mandate.agent import BuyerAgent

    args = inspect.signature(BuyerAgent._create_cart).parameters
    assert set(args) == {"self", "item_names", "merchant"}


# --- product photographs ------------------------------------------------------


def test_every_line_the_user_reads_carries_a_photograph(client):
    """List, offers and the decision cart. The mock's products are real products
    — it invents their prices, not their existence — so the merchant's own
    photography is the accurate picture rather than an approximation."""
    listed = client.get("/api/list/usual").json()["items"]
    assert listed and all(i["image_url"].startswith("https://") for i in listed)

    offers = client.get("/api/catalog?q=atta").json()["offers"]
    assert offers and all(o["image_url"].startswith("https://") for o in offers)

    cart = propose(client, USUAL, 185_000)["items"]
    assert cart and all(i["image_url"].startswith("https://") for i in cart)


def test_the_product_page_shows_the_product(client):
    page = client.get("/m/instamart/p/Brown%20bread")
    assert page.status_code == 200
    assert "<img src='https://" in page.text


def test_the_same_product_wears_the_same_photograph_at_every_shop(client):
    """One picture per product, not per seller. The other shops sell the same
    thing at their own price."""
    rows = client.get("/api/catalog?q=atta").json()["offers"]
    by_name = {}
    for row in rows:
        by_name.setdefault(row["name"], set()).add(row["image_url"])
    assert all(len(urls) == 1 for urls in by_name.values())


# --- both backends answer the same questions ----------------------------------


def test_offer_rows_normalise_either_backend_shape(client):
    """The mock pairs a seller with an item because it has three sellers; Swiggy
    is one shop, so its offer *is* the product. `_offer_rows` reads both."""
    from types import SimpleNamespace

    swiggy_shaped = SimpleNamespace(
        name="Amul Taaza 200 ml", price_paise=1_700, category="groceries", image_url="https://x/y"
    )
    seller, item = web._offer_parts(swiggy_shaped)
    assert (seller, item.name) == ("instamart", "Amul Taaza 200 ml")

    mock_shaped = SimpleNamespace(merchant="blinkit", item=swiggy_shaped)
    assert web._offer_parts(mock_shaped) == ("blinkit", swiggy_shaped)


def test_a_list_edit_is_not_validated_against_a_live_catalog(client, monkeypatch):
    """One `search_products` per line is a dozen round trips to validate one
    edit. An unbuyable line is caught where it costs something instead — the
    cart comes back short and the engine rules on the cart that exists."""
    assert web._unstocked(["Ferrari"]) == ["Ferrari"]

    monkeypatch.setattr(web, "is_live", lambda: True)
    assert web._unstocked(["Ferrari"]) == []
