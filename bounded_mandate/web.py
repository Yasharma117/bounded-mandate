"""HTTP surface for the user-present half of the product.

Only mandate *registration* is exposed over HTTP, because registration is the
only part a human participates in. There is deliberately no endpoint that
charges: the engine debits an authorised token server-side, and putting a
"pay" route on the internet would recreate the confirm dialog this exists to
remove.

The key secret never leaves this process. The page receives `key_id`, which is
public by design, and receives it from the order response rather than from a
build-time environment variable.

    set -a; . ./.env; set +a
    uv run uvicorn bounded_mandate.web:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .compiler import compile_mandate, render
from .razorpay_gateway import GatewayAuthError, GatewayError, RazorpayGateway, SignatureMismatch

app = FastAPI(title="Bounded Mandate", docs_url="/api/docs")

PAGE = Path(__file__).parent / "static" / "register.html"

# Test-mode placeholders. A real deployment reads these off the signed-in user.
DEMO_CUSTOMER = {
    "name": "Bounded Mandate Demo",
    "email": "demo@bounded-mandate.test",
    "contact": "9999999999",
}


def gateway() -> RazorpayGateway:
    try:
        return RazorpayGateway()
    except GatewayError as exc:
        raise HTTPException(503, str(exc)) from exc


class Utterance(BaseModel):
    text: str = Field(min_length=1)


class OrderRequest(BaseModel):
    max_amount_paise: int = Field(gt=0, description="The user's per-order cap, in paise")


class Callback(BaseModel):
    razorpay_order_id: str = Field(min_length=1)
    razorpay_payment_id: str = Field(min_length=1)
    razorpay_signature: str = Field(min_length=1)


@app.get("/", response_class=HTMLResponse)
def register_page() -> str:
    return PAGE.read_text(encoding="utf-8")


@app.post("/api/mandate/compile")
def compile_rule(body: Utterance) -> dict:
    """Plain language to a bounded contract. Scene 1 — nothing is registered yet."""
    compiled = compile_mandate(
        body.text, mandate_id="mdt_demo", delivery_addresses=frozenset({"home"})
    )
    draft = compiled.draft
    return {
        "source": compiled.source,
        "missing": list(compiled.missing),
        "registrable": compiled.policy is not None,
        "card": render(compiled),
        "bounds": {
            "per_txn_max_paise": draft.per_txn_max_paise,
            "cadence_days": draft.cadence_days,
            "merchants": draft.merchants,
            "categories": draft.categories,
        },
    }


@app.post("/api/mandate/order")
def create_mandate_order(body: OrderRequest) -> dict:
    """The ₹1 UPI Autopay authorisation order. RBI's one-time AFA moment."""
    gw = gateway()
    try:
        customer_id = gw.create_customer(**DEMO_CUSTOMER)
        order = gw.create_mandate_order(customer_id, max_amount_paise=body.max_amount_paise)
    except GatewayAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except GatewayError as exc:
        # A cap outside Razorpay's permitted range is the caller's fault, not ours.
        status = 400 if "mandate cap must be between" in str(exc) else 500
        raise HTTPException(status, str(exc)) from exc

    return {
        "order_id": order.order_id,
        "customer_id": order.customer_id,
        "amount": order.amount_paise,
        "currency": "INR",
        "key_id": order.key_id,
    }


@app.post("/api/mandate/verify")
def verify_registration(body: Callback) -> dict:
    """Nothing is registered until this passes. A bad signature registers nothing."""
    gw = gateway()
    try:
        gw.verify_registration(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        )
    except SignatureMismatch as exc:
        raise HTTPException(400, "signature verification failed") from exc
    except GatewayError as exc:
        raise HTTPException(500, str(exc)) from exc

    try:
        token_id = gw.token_for(body.razorpay_payment_id)
    except GatewayError:
        token_id = None  # verified, but the token is not readable yet

    return {"verified": True, "token_id": token_id, "payment_id": body.razorpay_payment_id}


@app.post("/api/webhook/razorpay")
async def razorpay_webhook(request: Request, secret: str = Body(default="", embed=True)) -> dict:
    """Unverified webhooks are discarded. Configure RAZORPAY_WEBHOOK_SECRET to use this."""
    import os

    configured = os.environ.get("RAZORPAY_WEBHOOK_SECRET", secret)
    if not configured:
        raise HTTPException(503, "RAZORPAY_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    try:
        gateway().verify_webhook(raw, request.headers.get("X-Razorpay-Signature", ""), configured)
    except SignatureMismatch as exc:
        raise HTTPException(400, "webhook signature did not verify") from exc
    return {"received": True}
