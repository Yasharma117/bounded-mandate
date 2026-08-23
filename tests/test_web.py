"""The HTTP surface. What matters most is what it refuses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bounded_mandate import web
from bounded_mandate.ledger import Ledger
from bounded_mandate.merchant import USUAL_GROCERIES, MockMerchant
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
    monkeypatch.setattr(web, "MERCHANT", MockMerchant())
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
