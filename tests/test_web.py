"""The HTTP surface. What matters most is what it refuses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bounded_mandate import web
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


@pytest.fixture
def client(monkeypatch):
    gw = FakeGateway()
    monkeypatch.setattr(web, "RazorpayGateway", lambda *a, **k: gw)
    c = TestClient(web.app)
    c.gateway = gw
    return c


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


def test_there_is_no_endpoint_that_charges(client):
    """Charging is user-absent by design. An HTTP route for it would be the
    confirm dialog this product exists to remove."""
    paths = {r.path for r in web.app.routes}
    forbidden = {p for p in paths if "charge" in p or p.rstrip("/").endswith("/pay")}
    assert not forbidden, forbidden


def test_webhooks_are_refused_until_a_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    assert client.post("/api/webhook/razorpay", json={}).status_code == 503
