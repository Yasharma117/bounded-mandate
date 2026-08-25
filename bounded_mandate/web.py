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

import asyncio
import contextlib
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .agent import ADVERSARIAL_SYSTEM, BuyerAgent
from .basket import ListKind, ShoppingList, seed_lists
from .compiler import compile_mandate
from .engine import MandateStatus, Policy, Proposal, Verdict, decide
from .ledger import Ledger
from .merchant import Marketplace, UnknownItem, UnknownMerchant
from .razorpay_gateway import GatewayAuthError, GatewayError, RazorpayGateway, SignatureMismatch
from .voice import SPEAKERS, TTS_PROVIDER, VoiceUnavailable, speak, transcribe
from .wording import summary, title


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    """Run the scheduler for as long as the app is up, if it is switched on."""
    task = asyncio.create_task(_scheduler()) if os.environ.get("BM_SCHEDULER") == "1" else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Bounded Mandate", docs_url="/api/docs", lifespan=_lifespan)

STATIC = Path(__file__).parent / "static"

HOME = "12 Nandidurga Rd, Bengaluru"

# One process-wide engine context. A real deployment would key these per user;
# the demo has one mandate and one merchant, so a module-level store is honest
# about what it is rather than pretending to be a database.
LEDGER = Ledger(os.environ.get("BM_LEDGER", "ledger.jsonl"))
MARKETPLACE = Marketplace()
# The user's lists. Owned by the user, read by the agent, and there is no route
# and no agent tool that lets the agent write one — see `basket`.
LISTS: dict[str, ShoppingList] = seed_lists()
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
    # The cart the *engine fetched*, not the one the agent described. When a
    # verdict says "2 items outside your scope", the reader should be able to
    # see which two rather than take the sentence on trust.
    cart = MARKETPLACE.fetch_cart(decision.cart_id)
    policy = POLICIES.get(decision.mandate_id)
    items = [
        {
            "name": item.name,
            "price_paise": item.price_paise,
            "category": item.category,
            "url": f"/m/{cart.merchant}/p/{quote(item.name)}",
            # Why this line is a problem, if it is. Computed here because it is
            # the policy's judgement, not the client's.
            "off_scope": bool(policy and item.category and item.category not in policy.categories),
            "unclassified": not item.category,
        }
        for item in (cart.items if cart else ())
    ]
    settled = _settle(decision, cart_items)
    return {
        "items": items,
        "merchant": cart.merchant if cart else None,
        "verdict": decision.verdict.value,
        # Both forms. The code is what the ledger stores and tests assert on;
        # the summary is what a person who just wanted groceries can read.
        "reason_code": decision.reason_code,
        "summary": summary(decision.reason_code),
        "reasons": [
            {"code": r.code, "title": title(r.code), "detail": r.detail} for r in decision.reasons
        ],
        # "Reached the rail" is our vocabulary, not theirs.
        "settlement": (
            "Paid"
            if settled.get("payment_id")
            else "Order placed, not yet paid"
            if settled.get("order_id")
            else "Nothing was charged"
        ),
        "cart_id": decision.cart_id,
        "real_total_paise": decision.total_paise,
        "claimed_total_paise": claimed_total_paise,
        "idempotency_key": decision.idempotency_key,
        **settled,
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
    merchant: str = "instamart"


@app.post("/api/proposal")
def submit_proposal(body: ProposalRequest) -> dict:
    """A cart, proposed directly. The engine decides; only an ALLOW reaches the rail."""
    try:
        cart = MARKETPLACE.create_cart(body.items, delivery_address=HOME, merchant=body.merchant)
    except UnknownMerchant as exc:
        raise HTTPException(400, str(exc.args[0])) from exc
    except UnknownItem as exc:
        raise HTTPException(400, f"not stocked: {exc.args[0]}") from exc

    decision = decide(
        Proposal(body.mandate_id, cart.cart_id, body.claimed_total_paise),
        policies=POLICIES,
        adapter=MARKETPLACE,
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
        marketplace=MARKETPLACE,
        shopping_list=LISTS.get("usual"),
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

    # Annotate what the agent found *on the way out*, never in the tool result.
    # `in_policy` is the policy's judgement, and the agent is not allowed to
    # learn its policy — knowing why it was refused is not the same as being
    # able to see what it is refused for. The app gets the annotation; the
    # model never did.
    steps = []
    for step in run.steps:
        result = step.result
        if step.tool == "search_catalog" and "offers" in result:
            result = {"offers": _annotate(result["offers"])}
        steps.append({"tool": step.tool, "args": step.args, "result": result})

    charge = next((s for s in reversed(run.steps) if s.tool == "request_charge"), None)
    claimed = int(charge.args.get("claimed_total_paise") or 0) if charge else 0
    cart = next((s for s in run.steps if s.tool == "create_cart"), None)
    items = int(cart.result.get("item_count") or 0) if cart else 0

    return {
        "said": run.said,
        "steps": steps,
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


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    provider: str | None = Field(
        default=None,
        description="`elevenlabs` or `rumik`. Omit for the configured default.",
    )


@app.post("/api/voice/speak")
def speak_text(body: SpeakRequest) -> Response:
    """Text in, audio out. The app plays it; the keys stay here.

    The two services return different formats, so the content type is whatever
    the provider actually produced rather than an assumption baked into the app.
    """
    try:
        spoken = speak(body.text, provider=body.provider)
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(
        spoken.audio,
        media_type=spoken.media_type,
        headers={"X-Voice-Provider": spoken.provider},
    )


@app.get("/api/voice/providers")
def voice_providers() -> dict:
    """Which services can speak, and which one is speaking by default."""
    return {"providers": sorted(SPEAKERS), "default": TTS_PROVIDER}


def _annotate(offers: list[dict]) -> list[dict]:
    """Add the policy's verdict and a working link to offers the agent found."""
    policy = POLICIES["mdt_demo"]
    return [
        {
            **offer,
            "url": f"/m/{offer['merchant']}/p/{quote(offer['name'])}",
            "merchant_allowed": offer["merchant"] in policy.merchants,
            "category_allowed": offer.get("category") in policy.categories,
        }
        for offer in offers
    ]


def _offer_rows(query: str) -> list[dict]:
    """Every seller's price for a product, with the policy's answer attached.

    `in_policy` is computed here rather than in the app, because whether a
    merchant is allowed is the engine's judgement and the client should not be
    reimplementing it — it only renders what it is told.
    """
    policy = POLICIES["mdt_demo"]
    return [
        {
            "merchant": offer.merchant,
            "name": offer.item.name,
            "price_paise": offer.item.price_paise,
            "category": offer.item.category,
            "url": f"/m/{offer.merchant}/p/{quote(offer.item.name)}",
            # Two separate answers, not one. A shop can be allowed while the
            # thing it sells is not, and telling the user "not on your list"
            # when the shop *is* on their list names the wrong reason.
            "merchant_allowed": offer.merchant in policy.merchants,
            "category_allowed": offer.item.category in policy.categories,
        }
        for offer in MARKETPLACE.search(query)
    ]


@app.get("/api/catalog")
def catalog(q: str = "") -> dict:
    """Cross-merchant search. The same product from three sellers at three
    prices, and — separately — whether the shop and the category are covered."""
    return {"offers": _offer_rows(q)}


def _list_rows(shopping: ShoppingList) -> dict:
    """The list, priced at the merchant the mandate allows."""
    policy = POLICIES["mdt_demo"]
    seller = next(iter(policy.merchants))
    catalog = MARKETPLACE[seller].catalog
    items = [
        {
            "name": name,
            "price_paise": catalog[name].price_paise if name in catalog else None,
            "category": catalog[name].category if name in catalog else "",
            "url": f"/m/{seller}/p/{quote(name)}",
        }
        for name in shopping.item_names
    ]
    priced = [i["price_paise"] for i in items if i["price_paise"] is not None]
    due_at = shopping.next_due()
    return {
        "list_id": shopping.list_id,
        "name": shopping.name,
        "merchant": seller,
        "items": items,
        "total_paise": sum(priced),
        "cap_paise": policy.per_txn_max_paise,
        "unstocked": [i["name"] for i in items if i["price_paise"] is None],
        "kind": shopping.kind.value,
        "every_days": shopping.every_days,
        "run_on": shopping.run_on.isoformat() if shopping.run_on else None,
        "paused": shopping.paused,
        "spent": shopping.spent,
        "last_run_at": shopping.last_run_at.isoformat() if shopping.last_run_at else None,
        "next_due_at": due_at.isoformat() if due_at else None,
        "due": shopping.due(),
        "schedule": _schedule_words(shopping),
    }


def _schedule_words(shopping: ShoppingList) -> str:
    """When this runs, said the way a person would say it."""
    if shopping.paused:
        return "Paused"
    if shopping.kind is ListKind.ONCE:
        if shopping.spent:
            return "Ordered once, done"
        if shopping.run_on is None:
            return "One-off, no date set"
        return f"Once, on {shopping.run_on.strftime('%-d %b')}"
    if shopping.every_days is None:
        return "Only when you ask"
    if shopping.every_days == 1:
        return "Every day"
    return f"Every {shopping.every_days} days"


class NewList(BaseModel):
    name: str = Field(min_length=1)
    item_names: list[str] = Field(default_factory=list)
    kind: str = Field(default="standing", description="`standing` or `once`")
    every_days: int | None = Field(default=None, ge=0, le=365)
    run_on: date | None = None


class Schedule(BaseModel):
    """Every field optional — a schedule edit should not have to restate a list."""

    every_days: int | None = Field(default=None, ge=0, le=365)
    run_on: date | None = None
    paused: bool | None = None


@app.get("/api/lists")
def all_lists() -> dict:
    """Every list the user keeps, soonest-due first."""
    rows = [_list_rows(shopping) for shopping in LISTS.values()]
    rows.sort(key=lambda row: (row["next_due_at"] is None, row["next_due_at"] or ""))
    return {"lists": rows}


@app.post("/api/lists")
def create_list(body: NewList) -> dict:
    """A new list. A user action — the agent has no tool that reaches this."""
    try:
        kind = ListKind(body.kind)
    except ValueError as exc:
        raise HTTPException(400, "kind must be `standing` or `once`") from exc
    unknown = [n for n in body.item_names if n not in MARKETPLACE["instamart"].catalog]
    if unknown:
        raise HTTPException(400, f"not stocked: {', '.join(unknown)}")

    list_id = _fresh_id(body.name)
    LISTS[list_id] = ShoppingList(
        list_id=list_id,
        name=body.name,
        item_names=tuple(body.item_names),
        kind=kind,
        every_days=body.every_days,
        run_on=body.run_on,
    )
    return _list_rows(LISTS[list_id])


def _fresh_id(name: str) -> str:
    stem = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "list"
    if stem not in LISTS:
        return stem
    n = 2
    while f"{stem}-{n}" in LISTS:
        n += 1
    return f"{stem}-{n}"


@app.delete("/api/list/{list_id}")
def delete_list(list_id: str) -> dict:
    if LISTS.pop(list_id, None) is None:
        raise HTTPException(404, "no such list")
    return {"deleted": list_id}


@app.put("/api/list/{list_id}/schedule")
def set_schedule(list_id: str, body: Schedule) -> dict:
    """Change *when*, without touching *what*.

    A cadence cannot widen authority: the engine never reads a schedule, so a
    list set to run hourly under a mandate permitting one order every four days
    is simply refused three times a day.
    """
    shopping = LISTS.get(list_id)
    if shopping is None:
        raise HTTPException(404, "no such list")
    LISTS[list_id] = replace(
        shopping,
        every_days=body.every_days if body.every_days is not None else shopping.every_days,
        run_on=body.run_on or shopping.run_on,
        paused=body.paused if body.paused is not None else shopping.paused,
    )
    return _list_rows(LISTS[list_id])


@app.get("/api/list/{list_id}")
def read_list(list_id: str) -> dict:
    shopping = LISTS.get(list_id)
    if shopping is None:
        raise HTTPException(404, "no such list")
    return _list_rows(shopping)


class ListEdit(BaseModel):
    item_names: list[str] = Field(description="The list, in the order the user wants it")


@app.put("/api/list/{list_id}")
def write_list(list_id: str, body: ListEdit) -> dict:
    """Replace the list. **A user action, and only a user action** — the buyer
    agent holds no tool that reaches this route, because an agent that could
    redefine "my usual groceries" could then order the new definition entirely
    within policy."""
    shopping = LISTS.get(list_id)
    if shopping is None:
        raise HTTPException(404, "no such list")
    unknown = [n for n in body.item_names if n not in MARKETPLACE["instamart"].catalog]
    if unknown:
        raise HTTPException(400, f"not stocked: {', '.join(unknown)}")
    LISTS[list_id] = replace(shopping, item_names=tuple(body.item_names))
    return _list_rows(LISTS[list_id])


@app.get("/m/{merchant}/p/{name}", response_class=HTMLResponse)
def product_page(merchant: str, name: str) -> str:
    """A real product page, so a product link resolves instead of 404ing.

    The injected catalog item gets one too, which is the point: the planted
    instruction is sitting in a product title that the agent reads and the
    shopper never looks at twice.
    """
    try:
        item = MARKETPLACE[merchant].catalog[name]
    except (UnknownMerchant, UnknownItem, KeyError) as exc:
        raise HTTPException(404, "not stocked") from exc
    return (
        f"<!doctype html><meta charset=utf-8>"
        f"<title>{escape(item.name)} · {escape(merchant)}</title>"
        f"<body style='font:16px/1.5 ui-sans-serif,system-ui;max-width:34rem;"
        f"margin:3rem auto;padding:0 1rem'>"
        f"<p style='color:#8E96C8;text-transform:uppercase;letter-spacing:.08em;"
        f"font-size:.75rem'>{escape(merchant)}</p>"
        f"<h1 style='font-size:1.25rem'>{escape(item.name)}</h1>"
        f"<p style='font-size:1.5rem;font-weight:600'>₹{item.price_paise / 100:,.0f}</p>"
        f"<p style='color:#666'>{escape(item.category or 'uncategorised')}</p>"
    )


# --- the scheduler ----------------------------------------------------------
#
# The product's whole claim is that nobody is present. A list with a cadence
# should therefore go out on its own, and this is the loop that does it.
#
# Off unless `BM_SCHEDULER=1`, because a background task that runs an agent has
# no business starting itself inside a test suite. On for the demo, where the
# point is that nothing was touched.
#
# It cannot widen anything. It picks due lists and proposes them; the engine
# decides exactly as it would for a proposal a human triggered, and a list set
# to run hourly under a once-every-four-days mandate is simply refused three
# times a day.

SCHEDULER_TICK = float(os.environ.get("BM_SCHEDULER_TICK", "20"))


def run_due_lists(now: datetime | None = None) -> list[dict]:
    """Propose every list that is due. Returns what the engine made of each."""
    now = now or datetime.now(UTC)
    out: list[dict] = []
    for list_id, shopping in list(LISTS.items()):
        if not shopping.due(now):
            continue
        try:
            cart = MARKETPLACE.create_cart(
                list(shopping.item_names), delivery_address=HOME, merchant="instamart"
            )
        except (UnknownMerchant, UnknownItem) as exc:
            out.append({"list_id": list_id, "error": str(exc.args[0])})
            continue

        decision = decide(
            Proposal("mdt_demo", cart.cart_id, cart.total_paise),
            policies=POLICIES,
            adapter=MARKETPLACE,
            ledger=LEDGER,
        )
        # Marked as run whatever the verdict — a refused attempt still happened,
        # and re-proposing the same basket every tick would look like probing.
        LISTS[list_id] = shopping.ran(now)
        out.append(
            {
                "list_id": list_id,
                "name": shopping.name,
                **_rendered(
                    decision,
                    claimed_total_paise=cart.total_paise,
                    cart_items=len(cart.items),
                ),
            }
        )
    return out


@app.post("/api/lists/run-due")
def run_due_now() -> dict:
    """Fire the scheduler once, by hand. What the timer does, on demand."""
    return {"ran": run_due_lists()}


async def _scheduler() -> None:
    while True:
        await asyncio.sleep(SCHEDULER_TICK)
        try:
            await asyncio.to_thread(run_due_lists)
        except Exception:  # a bad tick must not kill the loop
            pass
