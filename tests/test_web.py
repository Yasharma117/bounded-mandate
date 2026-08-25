"""The HTTP surface. What matters most is what it refuses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bounded_mandate import web
from bounded_mandate.basket import USUAL_GROCERIES, seed_lists
from bounded_mandate.engine import Verdict
from bounded_mandate.ledger import Ledger
from bounded_mandate.merchant import Marketplace
from bounded_mandate.razorpay_gateway import GatewayAuthError, GatewayError, SignatureMismatch


class FakeGateway:
    def __init__(self, *, order_error=None, verify_error=None):
        self.order_error, self.verify_error = order_error, verify_error
        self.charged = []

    def create_customer(self, **_):
        return "cust_1"

    def create_mandate_order(self, customer_id, *, max_amount_paise, **_):
        if self.order_error:
            raise self.order_error
        if not 100 <= max_amount_paise <= 10_000_000:
            raise GatewayError("mandate cap must be between 100 and 10000000 paise")
        return SimpleNamespace(
            order_id="order_1", customer_id=customer_id, amount_paise=100, key_id="rzp_test_key"
        )

    def verify_registration(self, *_):
        if self.verify_error:
            raise self.verify_error

    def token_for(self, _):
        return "token_abc"

    key_id = "rzp_test_key"

    def create_charge_order(self, *, amount_paise, idempotency_key, description):
        self.charged.append(amount_paise)
        return "order_charge_1"


@pytest.fixture
def client(monkeypatch, tmp_path):
    gw = FakeGateway()
    monkeypatch.setattr(web, "RazorpayGateway", lambda *a, **k: gw)
    monkeypatch.setattr(web, "LEDGER", Ledger(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(web, "MARKETPLACE", Marketplace())
    monkeypatch.setattr(web, "LISTS", seed_lists())
    c = TestClient(web.app)
    c.gateway = gw
    return c


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
    only as a consequence of a proposal the engine allowed."""
    paths = {r.path for r in web.app.routes}
    forbidden = {p for p in paths if "charge" in p or p.rstrip("/").endswith("/pay")}
    assert not forbidden, forbidden


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
