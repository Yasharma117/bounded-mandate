"""Settlement. The signature checks are the only security-critical part."""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

import pytest
import razorpay

from bounded_mandate.razorpay_gateway import (
    AUTH_AMOUNT_PAISE,
    MAX_MANDATE_AMOUNT_PAISE,
    GatewayAuthError,
    GatewayError,
    RazorpayGateway,
    SignatureMismatch,
)

SECRET = "test_secret_value"


def sign(message: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def gateway(**stubs) -> RazorpayGateway:
    """A gateway over a real SDK client (so signature logic is genuinely exercised)
    with the network-touching resources replaced."""
    client = razorpay.Client(auth=("rzp_test_key", SECRET))
    for name, value in stubs.items():
        setattr(client, name, value)
    return RazorpayGateway("rzp_test_key", SECRET, client=client)


# --- signatures --------------------------------------------------------------


def test_a_genuine_callback_verifies():
    gw = gateway()
    gw.verify_registration("order_1", "pay_1", sign("order_1|pay_1"))  # does not raise


def test_a_forged_signature_is_rejected():
    gw = gateway()
    with pytest.raises(SignatureMismatch):
        gw.verify_registration("order_1", "pay_1", sign("order_1|pay_1", "wrong_secret"))


def test_a_replayed_signature_from_another_order_is_rejected():
    """The signature binds order and payment together, not either alone."""
    gw = gateway()
    with pytest.raises(SignatureMismatch):
        gw.verify_registration("order_2", "pay_1", sign("order_1|pay_1"))


def test_webhook_signature_covers_the_exact_body():
    gw = gateway()
    body = b'{"event":"payment.captured"}'
    gw.verify_webhook(body, sign(body.decode()), SECRET)

    with pytest.raises(SignatureMismatch):
        gw.verify_webhook(b'{"event":"payment.failed"}', sign(body.decode()), SECRET)


# --- bounds ------------------------------------------------------------------


@pytest.mark.parametrize("cap", [0, 99, MAX_MANDATE_AMOUNT_PAISE + 1])
def test_a_cap_outside_razorpays_range_is_refused_before_the_call(cap):
    """Caught locally — no point spending a round trip to be told no."""
    called = []
    gw = gateway(order=SimpleNamespace(create=lambda d: called.append(d)))
    with pytest.raises(GatewayError, match="mandate cap must be between"):
        gw.create_mandate_order("cust_1", max_amount_paise=cap)
    assert called == []


def test_the_mandate_order_carries_the_token_and_charges_one_rupee():
    sent = {}
    gw = gateway(order=SimpleNamespace(create=lambda d: sent.update(d) or {"id": "order_9"}))
    order = gw.create_mandate_order("cust_1", max_amount_paise=200_000)

    assert order.order_id == "order_9"
    assert sent["amount"] == AUTH_AMOUNT_PAISE  # ₹1 authorisation, not the cap
    assert sent["method"] == "upi"
    assert sent["token"]["max_amount"] == 200_000
    assert sent["token"]["frequency"] == "as_presented"  # variable amount per debit


def test_the_key_secret_is_not_on_the_object_the_page_receives():
    gw = gateway(order=SimpleNamespace(create=lambda d: {"id": "order_9"}))
    order = gw.create_mandate_order("cust_1", max_amount_paise=200_000)
    assert SECRET not in repr(order)
    assert order.key_id == "rzp_test_key"


def test_a_charge_below_the_floor_is_refused():
    gw = gateway()
    with pytest.raises(GatewayError, match="at least"):
        gw.charge(
            token_id="tok_1",
            customer_id="cust_1",
            email="a@b.c",
            contact="9999999999",
            amount_paise=99,
            description="x",
            idempotency_key="k",
        )


# --- error classification ----------------------------------------------------


def test_bad_credentials_surface_as_an_auth_error():
    def boom(_):
        raise razorpay.errors.BadRequestError("Authentication failed")

    gw = gateway(customer=SimpleNamespace(create=boom))
    with pytest.raises(GatewayAuthError):
        gw.create_customer("n", "e@x.c", "9999999999")


def test_other_failures_stay_generic():
    def boom(_):
        raise razorpay.errors.ServerError("upstream on fire")

    gw = gateway(customer=SimpleNamespace(create=boom))
    with pytest.raises(GatewayError) as caught:
        gw.create_customer("n", "e@x.c", "9999999999")
    assert not isinstance(caught.value, GatewayAuthError)


def test_missing_credentials_fail_loudly(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(GatewayError, match="must be set"):
        RazorpayGateway()
