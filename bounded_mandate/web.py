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

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .agent import ADVERSARIAL_SYSTEM, BuyerAgent
from .compiler import compile_mandate
from .engine import MandateStatus, Policy, Proposal, Verdict, decide
from .ledger import Ledger
from .merchant import USUAL_GROCERIES, MockMerchant
from .razorpay_gateway import GatewayAuthError, GatewayError, RazorpayGateway, SignatureMismatch
from .voice import VoiceUnavailable, speak, transcribe

app = FastAPI(title="Bounded Mandate", docs_url="/api/docs")

STATIC = Path(__file__).parent / "static"

HOME = "12 Nandidurga Rd, Bengaluru"

# One process-wide engine context. A real deployment would key these per user;
# the demo has one mandate and one merchant, so a module-level store is honest
# about what it is rather than pretending to be a database.
LEDGER = Ledger(os.environ.get("BM_LEDGER", "ledger.jsonl"))
MERCHANT = MockMerchant()
# Demo baskets. Temporary scaffolding: the buyer agent will build carts itself,
# and these go with the buttons that call them.
SCENARIOS: dict[str, dict] = {
    "honest": {
        "label": "The usual basket — ₹1,850",
        "note": "12 groceries, reported truthfully",
        "items": list(USUAL_GROCERIES),
        "claimed_total_paise": 185_000,
    },
    "overcap": {
        "label": "Basket with earbuds and a phone case — ₹2,400",
        "note": "over cap, and two items off-scope",
        "items": [*USUAL_GROCERIES, "Bluetooth earbuds", "Phone case"],
        "claimed_total_paise": 240_000,
    },
    "lying": {
        "label": "Claim ₹1,850, hide a ₹15,000 smartwatch",
        "note": "the agent lies about its own cart",
        "items": [*USUAL_GROCERIES, "Smartwatch"],
        "claimed_total_paise": 185_000,
    },
}

POLICIES: dict[str, Policy] = {
    "mdt_demo": Policy(
        mandate_id="mdt_demo",
        per_txn_max_paise=200_000,
        merchants=frozenset({"instamart"}),
        categories=frozenset({"groceries"}),
        delivery_addresses=frozenset({HOME}),
        max_charges_per_window=1,
        window_days=4,
        status=MandateStatus.ACTIVE,
    )
}

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


def _settle(decision, cart_items: int) -> dict:
    """An ALLOW is the only thing that reaches the rail. The order is created
    server-side with nobody present — that half of settlement needs no mandate.
    On an account with recurring enabled the engine would go on to debit the
    token silently; without one, the order is where the money leg stops."""
    if decision.verdict is not Verdict.ALLOW:
        return {"order_id": None, "key_id": None}
    gw = gateway()
    try:
        order_id = gw.create_charge_order(
            amount_paise=decision.total_paise,
            idempotency_key=decision.idempotency_key,
            description=f"Bounded Mandate · {cart_items} items",
        )
    except GatewayAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"order_id": order_id, "key_id": gw.key_id}


def _rendered(decision, *, claimed_total_paise: int, cart_items: int) -> dict:
    return {
        "verdict": decision.verdict.value,
        "reason_code": decision.reason_code,
        "reasons": [{"code": r.code, "detail": r.detail} for r in decision.reasons],
        "cart_id": decision.cart_id,
        "real_total_paise": decision.total_paise,
        "claimed_total_paise": claimed_total_paise,
        "idempotency_key": decision.idempotency_key,
        **_settle(decision, cart_items),
    }


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
    return (STATIC / "register.html").read_text(encoding="utf-8")


@app.get("/order", response_class=HTMLResponse)
def order_page() -> str:
    return (STATIC / "order.html").read_text(encoding="utf-8")


@app.get("/api/scenarios")
def scenarios() -> dict:
    """The baskets the demo buttons propose. Server-owned, so the page holds no
    copy of the catalog — and this whole endpoint dies when the agent lands."""
    return SCENARIOS


@app.get("/api/ledger")
def ledger_entries() -> dict:
    """The audit trail, and whether the chain still verifies."""
    rows = [{"seq": e.seq, "ts": e.ts, **e.payload} for e in LEDGER.entries()]
    try:
        LEDGER.verify()
        intact = True
    except Exception:
        intact = False
    return {"chain_intact": intact, "entries": rows[-20:]}


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


class ProposalRequest(BaseModel):
    items: list[str] = Field(
        min_length=1, description="Catalog item names the agent put in the cart"
    )
    claimed_total_paise: int = Field(gt=0, description="What the agent says the cart comes to")
    mandate_id: str = "mdt_demo"


@app.post("/api/proposal")
def submit_proposal(body: ProposalRequest) -> dict:
    """A cart, proposed directly. The engine decides; only an ALLOW reaches the rail."""
    try:
        cart = MERCHANT.create_cart(body.items, delivery_address=HOME)
    except KeyError as exc:
        raise HTTPException(400, f"not stocked: {exc.args[0]}") from exc

    decision = decide(
        Proposal(body.mandate_id, cart.cart_id, body.claimed_total_paise),
        policies=POLICIES,
        adapter=MERCHANT,
        ledger=LEDGER,
    )
    return _rendered(
        decision, claimed_total_paise=body.claimed_total_paise, cart_items=len(cart.items)
    )


class Instruction(BaseModel):
    text: str = Field(min_length=1, description="What the user said, typed or spoken")
    adversarial: bool = Field(
        default=False,
        description="Run the compromised agent instead. The engine is not told which.",
    )


@app.post("/api/agent")
def run_agent(body: Instruction) -> dict:
    """Hand an instruction to the buyer agent and report what it did.

    The agent shops and proposes. It holds no Razorpay tool and cannot read the
    policy it is governed by, so `adversarial` changes only what the agent tries
    — never what the engine permits.
    """
    agent = BuyerAgent(
        merchant=MERCHANT,
        policies=POLICIES,
        ledger=LEDGER,
        mandate_id="mdt_demo",
        delivery_address=HOME,
        system=ADVERSARIAL_SYSTEM if body.adversarial else None,
    )
    try:
        run = agent.run(body.text)
    except Exception as exc:  # a model outage is a 502, not a silent approval
        raise HTTPException(502, f"the agent could not run: {exc}") from exc

    charge = next((s for s in reversed(run.steps) if s.tool == "request_charge"), None)
    claimed = int(charge.args.get("claimed_total_paise") or 0) if charge else 0
    cart = next((s for s in run.steps if s.tool == "create_cart"), None)
    items = int(cart.result.get("item_count") or 0) if cart else 0

    return {
        "said": run.said,
        "steps": [{"tool": s.tool, "args": s.args, "result": s.result} for s in run.steps],
        "decision": (
            _rendered(run.decision, claimed_total_paise=claimed, cart_items=items)
            if run.decision
            else None
        ),
    }


@app.post("/api/settlement/verify")
def verify_settlement(body: Callback) -> dict:
    """Confirm a settlement callback and write the Razorpay reference to the ledger."""
    gw = gateway()
    try:
        gw.verify_registration(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        )
    except SignatureMismatch as exc:
        raise HTTPException(400, "signature verification failed") from exc

    LEDGER.append(
        {
            "event": "SETTLED",
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "signature_verified": True,
        }
    )
    return {"verified": True, "payment_id": body.razorpay_payment_id}


@app.post("/api/webhook/razorpay")
async def razorpay_webhook(request: Request) -> dict:
    """Unverified webhooks are discarded. Configure RAZORPAY_WEBHOOK_SECRET to use this."""
    configured = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not configured:
        raise HTTPException(503, "RAZORPAY_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    try:
        gateway().verify_webhook(raw, request.headers.get("X-Razorpay-Signature", ""), configured)
    except SignatureMismatch as exc:
        raise HTTPException(400, "webhook signature did not verify") from exc
    return {"received": True}


@app.post("/api/voice/transcribe")
async def transcribe_speech(request: Request) -> dict:
    """Raw audio bytes in, text out. The transcript is fed to the agent as an
    *utterance*, with no more standing than typing it — the engine still decides.

        curl --data-binary @clip.m4a http://127.0.0.1:8117/api/voice/transcribe
    """
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "no audio")
    try:
        return {"text": transcribe(audio)}
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/voice/speak")
def speak_text(body: Utterance) -> Response:
    """Text in, mp3 out. The app plays it; the key stays here."""
    try:
        return Response(speak(body.text), media_type="audio/mpeg")
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
