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
import json
import os
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .agent import ADVERSARIAL_SYSTEM, BuyerAgent
from .basket import ListKind, ShoppingList, seed_addresses, seed_lists
from .categories import FEES, KNOWN, with_fees
from .commerce import BACKEND, is_live
from .commerce import build as build_commerce
from .commerce import offer_parts as _offer_parts
from .compiler import GrantRefused, compile_mandate, grant_for_cart
from .engine import MandateStatus, Policy, Proposal, Verdict, decide
from .ledger import ChainBroken, Ledger
from .merchant import MERCHANT_NAME, UnknownItem, UnknownMerchant
from .razorpay_gateway import GatewayAuthError, GatewayError, RazorpayGateway, SignatureMismatch
from .swiggy import ADDRESS_ID as SWIGGY_ADDRESS_ID
from .swiggy import SwiggyUnavailable
from .swiggy_mcp import SwiggySessionError
from .voice import SPEAKERS, TTS_PROVIDER, VoiceUnavailable, speak, transcribe
from .wording import action, chip, summary, title


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    """Run the scheduler for as long as the app is up, if it is switched on."""
    load_grants()
    task = asyncio.create_task(_scheduler()) if os.environ.get("BM_SCHEDULER") == "1" else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Bounded Mandate", docs_url="/api/docs", lifespan=_lifespan)


@dataclass
class Grant:
    """A minted one-time approval, and the checkout it opened.

    The bounds live in `policy`, which is the same object a standing mandate
    compiles to — the engine cannot tell a grant from a rule, and does not need
    to. What this adds is the money side: which Razorpay order the user was sent
    to, and whether it has been paid.
    """

    grant_id: str
    policy: Policy
    cart_id: str
    amount_paise: int
    merchant: str
    order_id: str | None
    key_id: str | None
    #: Who Razorpay has the saved card against.
    #:
    #: On the grant rather than in a module global, because the global is empty
    #: after a restart and the checkout then arrives with no customer — which
    #: Razorpay reads as "new customer", so a card saved five minutes ago is
    #: offered back as a blank Card Number field.
    customer_id: str | None = None
    #: The lines that were approved, as they stood at that moment.
    #:
    #: A grant *is* a snapshot — you approved those lines at that total — so it
    #: carries them rather than refetching. Without this the checkout re-read a
    #: cart held in process memory, and an engine restart turned a live approval
    #: into "this basket has changed", which was not what had happened.
    items: list[dict] = field(default_factory=list)
    #: Set once a signed callback confirms the money moved. A grant pays once.
    payment_id: str | None = None

    def state(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        if self.payment_id:
            return "paid"
        if self.policy.expires_at and self.policy.expires_at <= now:
            return "expired"
        return "ready" if self.order_id else "refused"


STATIC = Path(__file__).parent / "static"

#: Where things get delivered, as an **address id**. The engine matches on this
#: and never on the prose — see `basket.Address` for why that distinction is
#: load-bearing rather than tidy.
#:
#: On the live path it starts at the pinned `SWIGGY_ADDRESS_ID`; on the mock, at
#: the first seeded address. Either way the *user* changes it, and there is no
#: agent tool that reaches the route which does.
#: Where the chosen delivery address is kept between runs.
#:
#: It was a module global and nothing else, so every restart silently reset it
#: to `SWIGGY_ADDRESS_ID` — you chose an address, the choice took, and the next
#: time the engine came up it was somewhere else, with nothing on screen to say
#: it had moved. That is the same fault the grants file exists to fix, and it
#: matters more here: an address is authority, and authority that quietly
#: reverts to a default is worse than authority that fails loudly.
DELIVERY_PATH = Path(os.environ.get("BM_DELIVERY", "delivery.json"))


def save_delivery() -> None:
    """Remember where the account holder said to deliver."""
    DELIVERY_PATH.write_text(
        json.dumps({"delivery_id": DELIVERY_ID, "backend": BACKEND}), encoding="utf-8"
    )


def load_delivery() -> str | None:
    """The remembered address, if it still belongs to this backend.

    Mock ids and Swiggy ids are different namespaces, so a file written by one
    must not be honoured by the other — restored across a backend switch it
    would pin an address the shop has never heard of, and every order would
    escalate on a doorstep nobody could find.
    """
    if not DELIVERY_PATH.exists():
        return None
    try:
        saved = json.loads(DELIVERY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if saved.get("backend") != BACKEND:
        return None
    return saved.get("delivery_id") or None


DELIVERY_ID: str = SWIGGY_ADDRESS_ID if is_live() else seed_addresses()[0].address_id
# A remembered choice outranks the environment default: the env var is where to
# start, not where the account holder decided to send things.
DELIVERY_ID = load_delivery() or DELIVERY_ID

# One process-wide engine context. A real deployment would key these per user;
# the demo has one mandate and one merchant, so a module-level store is honest
# about what it is rather than pretending to be a database.
LEDGER = Ledger(os.environ.get("BM_LEDGER", "ledger.jsonl"))
# `BM_COMMERCE=swiggy` swaps in real Instamart. Mock is the default so tests,
# CI and a recorded demo never depend on a five-day token.
MARKETPLACE = build_commerce()
# The user's lists. Owned by the user, read by the agent, and there is no route
# and no agent tool that lets the agent write one — see `basket`.
LISTS: dict[str, ShoppingList] = seed_lists()
#: The standing mandate. One in this build; a name rather than a literal so the
#: routes that read and write it cannot drift apart from the one that seeds it.
MANDATE_ID = "mdt_demo"

POLICIES: dict[str, Policy] = {
    MANDATE_ID: Policy(
        mandate_id=MANDATE_ID,
        per_txn_max_paise=200_000,
        merchants=frozenset({"instamart"}),
        categories=with_fees({"groceries"}),
        delivery_addresses=frozenset({DELIVERY_ID}),
        max_charges_per_window=1,
        window_days=4,
        status=MandateStatus.ACTIVE,
    )
}

# One-time grants, minted by the user and spent once. Kept apart from POLICIES
# only in that this half holds the *checkout* — the authority itself is an
# ordinary Policy in POLICIES, judged by the same `decide()` as everything else.
#
# Persisted, because they were not and it showed: restarting the engine while
# somebody had a checkout open answered their payment link with "no such
# approval". A fifteen-minute grant that a deploy can silently void is not a
# grant, and the reader had already done the one thing the product asks of them.
GRANTS: dict[str, Grant] = {}
GRANTS_PATH = Path(os.environ.get("BM_GRANTS", "grants.json"))


def _policy_json(policy: Policy) -> dict:
    return {
        "mandate_id": policy.mandate_id,
        "per_txn_max_paise": policy.per_txn_max_paise,
        "merchants": sorted(policy.merchants),
        "categories": sorted(policy.categories),
        "delivery_addresses": sorted(policy.delivery_addresses),
        "max_charges_per_window": policy.max_charges_per_window,
        "window_days": policy.window_days,
        "status": policy.status.value,
        "expires_at": policy.expires_at.isoformat() if policy.expires_at else None,
        "cart_id": policy.cart_id,
    }


def _policy_from(raw: dict) -> Policy:
    return Policy(
        mandate_id=raw["mandate_id"],
        per_txn_max_paise=raw["per_txn_max_paise"],
        merchants=frozenset(raw["merchants"]),
        categories=frozenset(raw["categories"]),
        delivery_addresses=frozenset(raw["delivery_addresses"]),
        max_charges_per_window=raw["max_charges_per_window"],
        window_days=raw["window_days"],
        status=MandateStatus(raw["status"]),
        expires_at=datetime.fromisoformat(raw["expires_at"]) if raw["expires_at"] else None,
        cart_id=raw.get("cart_id"),
    )


def save_grants() -> None:
    """Write every grant out. Called after anything that mints or spends one."""
    rows = [
        {
            "grant_id": g.grant_id,
            "policy": _policy_json(g.policy),
            "cart_id": g.cart_id,
            "amount_paise": g.amount_paise,
            "merchant": g.merchant,
            "order_id": g.order_id,
            "key_id": g.key_id,
            "customer_id": g.customer_id,
            "items": g.items,
            "payment_id": g.payment_id,
        }
        for g in GRANTS.values()
    ]
    GRANTS_PATH.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def load_grants() -> None:
    """Read them back, and put their policies where the engine looks.

    A grant restored without its policy would answer its checkout and then be
    refused by `decide()` as `mandate.unknown`, which is a worse failure than
    the one this fixes.
    """
    if not GRANTS_PATH.exists():
        return
    try:
        rows = json.loads(GRANTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    for raw in rows:
        policy = _policy_from(raw["policy"])
        GRANTS[raw["grant_id"]] = Grant(
            grant_id=raw["grant_id"],
            policy=policy,
            cart_id=raw["cart_id"],
            amount_paise=raw["amount_paise"],
            merchant=raw["merchant"],
            order_id=raw.get("order_id"),
            key_id=raw.get("key_id"),
            customer_id=raw.get("customer_id"),
            items=raw.get("items") or [],
            payment_id=raw.get("payment_id"),
        )
        POLICIES[raw["grant_id"]] = policy


# Test-mode placeholders. A real deployment reads these off the signed-in user.
# Left as it is deliberately. `9999999999` is refused by `POST /v1/payment_links`
# ("Recurring digits in customer contact are disallowed") and is an ugly thing to
# keep — but Standard Checkout accepts it, the saved card already sits against
# the customer this creates, and changing the contact mints a *different*
# customer that no token belongs to. That is a card the reader has to enter
# again, traded for tidiness.
DEMO_CUSTOMER = {
    "name": "Bounded Mandate Demo",
    "email": "demo@bounded-mandate.test",
    "contact": "9999999999",
}


#: Created once and reused. Every charge order is attached to it, which is what
#: lets Razorpay offer a saved card on the second checkout.
_CUSTOMER: str | None = None


def customer_id(gw: RazorpayGateway) -> str | None:
    """The Razorpay customer these orders belong to, or `None` if it cannot be
    made — a saved card is a convenience, and losing it must not lose the sale."""
    global _CUSTOMER
    if _CUSTOMER is None:
        try:
            _CUSTOMER = gw.create_customer(**DEMO_CUSTOMER)
        except GatewayError:
            return None
    return _CUSTOMER


#: Anything the commerce adapter raises when the shop cannot be reached. The
#: transport wraps its own errors into `SwiggyUnavailable`, but both are caught
#: so a future adapter cannot slip a bare exception past this.
SHOP_DOWN = (SwiggyUnavailable, SwiggySessionError)


def shop_down(exc: Exception) -> HTTPException:
    """A 503 carrying the adapter's own words.

    These used to escape as bare 500s — `Internal Server Error`, no reason —
    from `/api/catalog` and `/api/agent`, while `/api/lists` answered 200 as
    though the shop were merely empty. Five days after a token is issued that is
    the difference between a demo and a mystery, and the adapter already writes
    a message naming the expiry and the fix.
    """
    return HTTPException(503, str(exc))


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
            customer_id=customer_id(gw),
        )
    except GatewayAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"order_id": order_id, "key_id": gw.key_id}


def _cart_lines(cart, policy) -> list[dict]:
    """One basket, as every card renders it.

    Shared so the thread and the home screen cannot drift about what a line is —
    the flags especially, since `off_scope` is the policy's judgement and two
    surfaces computing it separately is two chances to disagree.
    """
    return [
        {
            "name": item.name,
            "price_paise": item.price_paise,
            "category": item.category,
            "url": f"/m/{cart.merchant}/p/{quote(item.name)}",
            # Decoration. Blank for a fee line, and for anything the merchant
            # has no picture of — the card renders nothing rather than a
            # placeholder box, so a missing photo costs no layout.
            "image_url": item.image_url,
            # Why this line is a problem, if it is. Computed here because it is
            # the policy's judgement, not the client's.
            "off_scope": bool(policy and item.category and item.category not in policy.categories),
            "unclassified": not item.category,
        }
        for item in (cart.items if cart else ())
    ]


def _rendered(decision, *, claimed_total_paise: int, cart_items: int) -> dict:
    # The cart the *engine fetched*, not the one the agent described. When a
    # verdict says "2 items outside your scope", the reader should be able to
    # see which two rather than take the sentence on trust.
    cart = MARKETPLACE.fetch_cart(decision.cart_id)
    items = _cart_lines(cart, POLICIES.get(decision.mandate_id))
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
        # The id, resolved to a label by the app against the book it already
        # holds. Rendering it here would mean an address-book call per decision,
        # which on the live path is a network round trip to print one word.
        "delivery_id": cart.delivery_address if cart else None,
        "real_total_paise": decision.total_paise,
        "claimed_total_paise": claimed_total_paise,
        "idempotency_key": decision.idempotency_key,
        **settled,
    }


class Utterance(BaseModel):
    text: str = Field(min_length=1)


class OrderRequest(BaseModel):
    max_amount_paise: int = Field(gt=0, description="The user's per-order cap, in paise")


#: A per-order cap above this is a typo, not a rule. The ceiling exists to catch
#: a stray zero at the boundary rather than to have an opinion about how much
#: somebody may spend — which is why it is generous and not clever.
MAX_CAP_PAISE = 10_000_000  # ₹1,00,000


class RuleEdit(BaseModel):
    """The standing rule as the *user* sets it. Every field is required.

    Not optional-and-merged like `Schedule`, on purpose: a partial edit to a
    bound means the caller is trusting a value they did not restate, and this
    is the one payload where every number has to be something a person put
    there deliberately. Restating all four is the point, not an inconvenience.
    """

    per_txn_max_paise: int = Field(gt=0, le=MAX_CAP_PAISE)
    merchants: list[str] = Field(min_length=1)
    categories: list[str] = Field(min_length=1)
    every_days: int = Field(ge=1, le=365)


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


#: The refusals worth counting separately, because "refused" as one number says
#: nothing about *why*. Each of these is a different claim about the product:
#: the agent lied, the agent repeated itself, the rule held a line.
_BLOCKS: dict[str, str] = {
    "provenance.total_mismatch": "misreported totals caught",
    "provenance.cart_not_found": "carts that did not exist",
    "duplicate.suppressed": "duplicate charges suppressed",
    "cap.exceeded": "over your cap",
    "category.not_allowed": "outside your categories",
    "merchant.not_allowed": "shops outside your rule",
    "delivery.unknown_address": "sent to an address you never authorised",
    "agent.probing": "bursts of probing escalated",
    "grant.other_cart": "approvals spent on a different basket",
}


@app.get("/api/stats")
def stats() -> dict:
    """What the engine has actually done, counted off the ledger it already keeps.

    Nothing here is recorded for the purpose of being counted — every figure is
    derived from decision entries written at the time, which is the point. A
    number that needed its own tracking could be wrong without the chain
    noticing; these cannot be, because they are the chain.

    So this route is deliberately a pure read. If it ever needs a write to
    answer a question, that question does not belong here.
    """
    decisions, settled = [], 0
    for entry in LEDGER.entries():
        payload = entry.payload
        if payload.get("event") == "SETTLED":
            settled += 1
        elif payload.get("verdict"):
            decisions.append(payload)

    allowed = [d for d in decisions if d["verdict"] == Verdict.ALLOW.value]
    refused = [d for d in decisions if d["verdict"] != Verdict.ALLOW.value]

    # By cart, not by decision: an agent that retries one refused basket has not
    # held back that money twice, and counting attempts would flatter the figure.
    #
    # How well this collapses is the merchant's choice, not ours. Swiggy's cart
    # ids are content-addressed — `sha256(lines ‖ total)` — so the identical
    # basket proposed twice is one id and counts once. The mock mints a fresh
    # sequential id per `create_cart`, so there it counts twice, which is the
    # merchant's own answer to "is this the same basket" and not ours to
    # second-guess.
    def summed(rows: list[dict]) -> int:
        return sum({d["cart_id"]: d.get("total_paise", 0) for d in rows}.values())

    codes = Counter(
        reason["code"] for d in decisions for reason in d.get("reasons", ()) if reason.get("code")
    )

    try:
        chain = {"entries": LEDGER.verify(), "intact": True}
    except ChainBroken as exc:
        chain = {"entries": 0, "intact": False, "detail": str(exc)}

    return {
        "decisions": len(decisions),
        "allowed": len(allowed),
        "refused": len(refused),
        "by_verdict": dict(Counter(d["verdict"] for d in decisions)),
        "authorised_paise": summed(allowed),
        # The number the product exists to produce: money an autonomous agent
        # asked for and did not get.
        "held_back_paise": summed(refused),
        "blocked": {label: codes[code] for code, label in _BLOCKS.items() if codes[code]},
        "settlements": settled,
        "chain": chain,
    }


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


@app.put("/api/mandate")
def set_rule(body: RuleEdit) -> dict:
    """Commit the standing rule. **This is where authority is created.**

    The compiler proposes and this decides — the same shape as the agent and the
    engine, applied to the one path where it was not. `/api/mandate/compile`
    reads a sentence with a model and hands back a draft to fill controls with;
    every bound that becomes authority arrives here, from those controls, as a
    number or a name the account holder set.

    Which closes the one seam in the design. A model that misread "₹2,000" as
    ₹2,00,000 used to produce a rule that looked right on a card and would have
    been enforced faithfully forever, and the only thing standing between that
    and the money was somebody reading carefully. Now the model cannot write a
    bound at all: it can be wrong in the draft, and the wrongness dies at the
    control the user touches.

    `delivery_addresses` is deliberately absent. It is authority too, and it has
    its own route (`PUT /api/address`) that checks the address is one the
    account actually holds — letting it in here would be a second way to set it,
    and the weaker one.
    """
    current = POLICIES[MANDATE_ID]
    clean = lambda names: frozenset(n.strip().casefold() for n in names if n.strip())  # noqa: E731
    merchants, categories = clean(body.merchants), clean(body.categories)
    if not merchants or not categories:
        raise HTTPException(422, "a rule needs at least one shop and one category")

    POLICIES[MANDATE_ID] = replace(
        current,
        per_txn_max_paise=body.per_txn_max_paise,
        merchants=merchants,
        # `with_fees` here for the same reason it is everywhere else: a delivery
        # charge is not a discretionary purchase, and a user who lists their
        # categories should not have to remember it.
        categories=with_fees(categories),
        window_days=body.every_days,
    )
    return _rule_view(POLICIES[MANDATE_ID])


@app.get("/api/mandate")
def read_rule() -> dict:
    """The standing rule as it is actually enforced, for the controls to open on."""
    return _rule_view(POLICIES[MANDATE_ID])


def _rule_view(policy: Policy) -> dict:
    """One shape for the rule, so what the controls show is what the engine holds."""
    return {
        "per_txn_max_paise": policy.per_txn_max_paise,
        # Sorted so the controls do not reshuffle between reads — `frozenset`
        # has no order and the card would flicker.
        "merchants": sorted(policy.merchants),
        # `fees` is added by the engine and is not the user's to edit, so it is
        # not shown as one of their choices.
        "categories": sorted(policy.categories - {FEES}),
        "every_days": policy.window_days,
        "orders_per_window": policy.max_charges_per_window,
        "delivery_addresses": sorted(policy.delivery_addresses),
        "max_cap_paise": MAX_CAP_PAISE,
        # The options the controls offer. Served rather than hardcoded in the
        # client so a shop the engine cannot reach is never presented as a
        # choice — and so the two cannot drift.
        "merchant_options": _shops(),
        "category_options": list(KNOWN),
    }


def _shops() -> list[str]:
    """Which shops this build can actually reach.

    Live is Instamart alone; the mock has three. Offering a rule the commerce
    backend cannot honour would be a control that produces refusals rather than
    purchases.
    """
    known = getattr(MARKETPLACE, "merchants", None)
    return sorted(known) if known else [MERCHANT_NAME]


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


# --- the one-time purchase --------------------------------------------------
#
# The standing rule covers the shopping. It does not cover the thing you needed
# once — a ₹4,000 air fryer is not groceries, is over the cap, and *should* be
# refused. The refusal is correct; it is not the end of the story.
#
# So: the user reads the basket the engine fetched and approves that one basket.
# What they get is not an exception to the rule and not a raised cap. It is a
# second mandate, minted from the cart, narrower than any sentence they could
# have said, alive for fifteen minutes, and spendable once.
#
# The engine is not told which kind it is judging.


class GrantRequest(BaseModel):
    cart_id: str = Field(min_length=1, description="The basket the engine fetched and refused")


def _standing_addresses() -> frozenset[str]:
    """Where a standing rule already ships.

    A grant may widen what and how much. It may not introduce an address, so
    this deliberately reads only the standing mandates — a grant cannot bootstrap
    the next grant's delivery scope.
    """
    return frozenset().union(
        *(p.delivery_addresses for p in POLICIES.values() if p.cart_id is None), frozenset()
    )


def _grant_bounds(grant: Grant) -> dict:
    """What the user is approving, as bounds rather than a price."""
    policy = grant.policy
    return {
        "grant_id": grant.grant_id,
        "per_txn_max_paise": policy.per_txn_max_paise,
        "merchants": sorted(policy.merchants),
        "categories": sorted(policy.categories),
        "delivery_address": next(iter(policy.delivery_addresses), ""),
        "every_days": policy.window_days,
        "orders_per_window": policy.max_charges_per_window,
        "cart_id": policy.cart_id,
        "expires_at": policy.expires_at.isoformat() if policy.expires_at else None,
        "state": grant.state(),
    }


@app.post("/api/mandate/one-time")
def grant_once(body: GrantRequest) -> dict:
    """Approve one basket, once.

    **A user action, and only a user action.** The buyer agent holds no tool
    that reaches this route — an agent able to mint its own authority is not
    governed by any.

    Every bound comes off the cart the *engine* fetched, so an agent that
    misreported its basket cannot get a grant written around the lie: the
    proposal that follows is judged against the real one, by the same `decide()`
    that refused it a moment ago.
    """
    cart = MARKETPLACE.fetch_cart(body.cart_id)
    if cart is None:
        raise HTTPException(404, "no such basket")

    # Approving twice should not mint twice. A live grant for this exact basket
    # is the answer to the same question, and a second Razorpay order for a
    # basket that is going to be paid once is just litter.
    for existing in GRANTS.values():
        if existing.cart_id == cart.cart_id and existing.state() == "ready":
            return {"grant": _grant_bounds(existing), "pay_url": f"/pay?grant={existing.grant_id}"}

    # Unguessable, because the pay URL *is* the capability.
    grant_id = "grant_" + secrets.token_urlsafe(12)
    try:
        policy = grant_for_cart(cart, grant_id=grant_id, authorised_addresses=_standing_addresses())
    except GrantRefused as exc:
        # Recorded rather than only returned. An attempt to approve a basket
        # bound for an address the rule does not cover is exactly the event the
        # delivery bound exists to catch, and a 403 the app swallows would
        # leave the user with nothing to look at afterwards.
        LEDGER.append(
            {
                "event": "HALTED",
                "reason_code": "delivery.unknown_address",
                "cart_id": cart.cart_id,
                "total_paise": cart.total_paise,
                "detail": str(exc),
            }
        )
        raise HTTPException(403, str(exc)) from exc
    POLICIES[grant_id] = policy

    # The grant is authority, not approval. The engine still rules on the
    # proposal, and an ALLOW here is what creates the order.
    decision = decide(
        Proposal(grant_id, cart.cart_id, cart.total_paise),
        policies=POLICIES,
        adapter=MARKETPLACE,
        ledger=LEDGER,
    )
    rendered = _rendered(decision, claimed_total_paise=cart.total_paise, cart_items=len(cart.items))
    grant = Grant(
        grant_id=grant_id,
        policy=policy,
        cart_id=cart.cart_id,
        amount_paise=decision.total_paise,
        merchant=cart.merchant,
        order_id=rendered.get("order_id"),
        key_id=rendered.get("key_id"),
        customer_id=_CUSTOMER,
        items=rendered.get("items") or [],
    )
    GRANTS[grant_id] = grant
    save_grants()
    return {
        "grant": _grant_bounds(grant),
        "decision": rendered,
        "pay_url": f"/pay?grant={grant_id}" if grant.order_id else None,
    }


def _saved_card(customer_id: str | None) -> str | None:
    """Never fatal: a checkout that cannot answer this still has to open."""
    if not customer_id:
        return None
    try:
        return gateway().saved_card(customer_id)
    except HTTPException:
        return None


@app.get("/api/grant/{grant_id}")
def read_grant(grant_id: str) -> dict:
    """What the checkout page needs, and no more than that."""
    grant = GRANTS.get(grant_id)
    if grant is None:
        raise HTTPException(
            404,
            "This approval is no longer here — it was spent, it lapsed, or the "
            "engine was restarted under it. Approve the basket again in the app "
            "and a fresh one will open. Nothing was charged.",
        )
    # On the live path cart ids are content-addressed, so a basket that moved
    # after the grant was minted no longer answers to the id the grant names.
    # That is not a missing cart, it is a *different* one, and the order sitting
    # on Razorpay is for a total nobody has looked at since — so it stops here.
    #
    # Only on the live path. The mock holds carts in process memory, where a
    # miss means the engine restarted rather than the basket changing, and
    # calling that "changed" sent people looking for a change that never
    # happened. The engine pins `cart_id` either way, so nothing can be
    # substituted for what was approved.
    moved = is_live() and MARKETPLACE.fetch_cart(grant.cart_id) is None
    state = "stale" if moved and not grant.payment_id else grant.state()
    return {
        **_grant_bounds(grant),
        "state": state,
        "merchant": grant.merchant,
        "amount_paise": grant.amount_paise,
        # Present only while the grant is spendable. A lapsed or stale approval
        # hands out no order id, so an old tab cannot open a checkout.
        "order_id": grant.order_id if state == "ready" else None,
        "key_id": grant.key_id if state == "ready" else None,
        # Everything the checkout may fill in on the user's behalf, which is
        # their contact details and nothing else. A card number is not on this
        # list and cannot be — see `create_charge_order`.
        "prefill": {k: v for k, v in DEMO_CUSTOMER.items() if k in ("name", "email", "contact")},
        "customer_id": grant.customer_id or _CUSTOMER,
        # What Checkout will actually offer, asked of the endpoint Checkout
        # itself reads. `None` means a full card, whatever the Customers API
        # says it holds.
        "saved_card": _saved_card(grant.customer_id or _CUSTOMER),
        "payment_id": grant.payment_id,
        # From the grant's own snapshot, so the page shows what was approved
        # rather than whatever the shop happens to hold now.
        "items": grant.items,
    }


@app.get("/pay", response_class=HTMLResponse)
def pay_page() -> str:
    """Real Razorpay Standard Checkout, for the half of the product that has a
    person in it. The standing mandate exists precisely so the *other* half does
    not need this page."""
    return (STATIC / "pay.html").read_text(encoding="utf-8")


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
        cart = MARKETPLACE.create_cart(body.items, merchant=body.merchant)
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


class Turn(BaseModel):
    """One thing that was said. Not a tool call, and not a cart id."""

    from_: str = Field(alias="from", description="`user` or `agent`")
    text: str = ""

    model_config = {"populate_by_name": True}


class Instruction(BaseModel):
    text: str = Field(min_length=1, description="What the user said, typed or spoken")
    history: list[Turn] = Field(
        default_factory=list,
        max_length=40,
        description="The conversation so far, so a follow-up means something.",
    )
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
    try:
        # Constructed inside the try: building one opens the model client, and a
        # missing provider key raised out of here as a bare 500 with no reason.
        agent = BuyerAgent(
            marketplace=MARKETPLACE,
            shopping_list=LISTS.get("usual"),
            policies=POLICIES,
            ledger=LEDGER,
            mandate_id="mdt_demo",
            delivery_address=DELIVERY_ID,
            system=ADVERSARIAL_SYSTEM if body.adversarial else None,
        )
        run = agent.run(
            body.text,
            history=[{"from": t.from_, "text": t.text} for t in body.history],
        )
    except SHOP_DOWN as exc:
        # The shop, not the model. Told apart because the answers differ: one
        # wants a retry, the other wants a new token.
        raise shop_down(exc) from exc
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

    # The draft, with the shop's answer on each line. A list the merchant does
    # not stock is a list that will escalate the first time it runs, and the
    # place to learn that is here rather than three days later.
    draft = None
    if run.draft is not None:
        unstocked = set(_unstocked(list(run.draft.item_names)))
        catalog = {} if is_live() else MARKETPLACE[MERCHANT_NAME].catalog
        draft = {
            "name": run.draft.name,
            "every_days": run.draft.every_days,
            "items": [
                {
                    "name": name,
                    "stocked": name not in unstocked,
                    "price_paise": catalog[name].price_paise if name in catalog else None,
                    "image_url": catalog[name].image_url if name in catalog else "",
                }
                for name in run.draft.item_names
            ],
        }

    return {
        "said": run.said,
        "steps": steps,
        "draft": draft,
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

    # The signature proves Razorpay sent this. It does not prove it is *ours* —
    # a valid triple replayed from any other flow on this account verifies just
    # as well. Without this guard a replayed registration callback wrote a
    # SETTLED entry that matched no grant, and the home card then read
    # "Paid — your order. It is on its way." about an order nobody placed.
    #
    # So authenticity and authorisation are checked separately, which is the
    # same split the engine makes everywhere else: the signature says who spoke,
    # the grant says whether it was allowed to.
    grant = next(
        (g for g in GRANTS.values() if g.order_id == body.razorpay_order_id and not g.payment_id),
        None,
    )
    if grant is None:
        raise HTTPException(400, "no open grant matches this order")

    # The grant is now spent. Razorpay would refuse a second payment on a paid
    # order anyway; revoking here means our own store says so too, rather than
    # relying on the rail to remember.
    grant.payment_id = body.razorpay_payment_id
    POLICIES[grant.grant_id] = replace(grant.policy, status=MandateStatus.REVOKED)
    save_grants()

    LEDGER.append(
        {
            "event": "SETTLED",
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "signature_verified": True,
            "grant_id": grant.grant_id,
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
    rows = []
    try:
        found = MARKETPLACE.search(query)
    except SHOP_DOWN as exc:
        raise shop_down(exc) from exc
    for offer in found:
        seller, item = _offer_parts(offer)
        rows.append(
            {
                "merchant": seller,
                "name": item.name,
                "price_paise": item.price_paise,
                "category": item.category,
                "url": f"/m/{seller}/p/{quote(item.name)}",
                "image_url": item.image_url,
                # Two separate answers, not one. A shop can be allowed while the
                # thing it sells is not, and telling the user "not on your list"
                # when the shop *is* on their list names the wrong reason.
                "merchant_allowed": seller in policy.merchants,
                "category_allowed": item.category in policy.categories,
            }
        )
    return rows


# --- one product, and what else would do -------------------------------------
#
# A line on a list is a name and a price, which is enough to decide *whether*
# but not enough to decide *which*. Tapping it should answer the second
# question — and the answer has to carry the policy's verdict with it, because
# an alternative the rule does not cover is a thing the user should learn now
# rather than after an escalation.

#: Words that describe a pack rather than a product. Dropped when widening a
#: name into a search, or "Toned milk 1L x2" only ever finds itself.
_PACK_WORDS = frozenset({"kg", "g", "gm", "ml", "l", "ltr", "litre", "pack", "x", "pc", "pcs"})


def _search_term(name: str) -> str:
    """A product name, widened into something that finds its neighbours.

    "Aashirvaad atta 5kg" -> "aashirvaad atta". Two words, because one is too
    broad ("amul" is half the dairy aisle) and three is usually the exact
    product again.
    """
    words = []
    for raw in re.split(r"[^a-z0-9]+", name.casefold()):
        word = raw.strip("0123456789.")
        if raw and word and word not in _PACK_WORDS:
            words.append(word)
    return " ".join(words[:2])


@app.get("/api/product")
def product_detail(name: str, merchant: str = MERCHANT_NAME) -> dict:
    """One product, its packs, and what else would do.

    Every pack carries **its own** verdict, because a cap is a number and packs
    have different ones: `1 ltr` at ₹77 clears a ₹2,000 rule and `1 ltr x 12` at
    ₹924 may not. Instamart's own sheet cannot say that; it is the one thing
    this version knows that the shop does not, and it turns a size selector into
    a place you can see what your rule reaches.

    `merchant_allowed` and `category_allowed` stay separate for the reason the
    offers card already keeps them separate — a shop can be allowed while the
    thing it sells is not, and collapsing them names the wrong reason on the one
    screen somebody opened in order to choose.
    """
    policy = POLICIES["mdt_demo"]
    try:
        listing, alternatives = MARKETPLACE.describe(name)
    except SHOP_DOWN as exc:
        raise shop_down(exc) from exc
    if listing is None:
        raise HTTPException(404, "not stocked")

    def pack(variant) -> dict:
        return {
            "sku_id": variant.sku_id,
            "name": variant.name,
            "label": variant.label,
            "price_paise": variant.price_paise,
            "mrp_paise": variant.mrp_paise,
            "off": variant.off,
            "unit_price": variant.unit_price,
            "in_stock": variant.in_stock,
            # The pack's own answer, which is the point of showing them together.
            "within_cap": variant.price_paise <= policy.per_txn_max_paise,
        }

    def described(one, seller: str) -> dict:
        return {
            "name": one.name,
            "brand": one.brand,
            "merchant": seller,
            "image_url": one.image_url,
            "category": one.category,
            "rating": one.rating,
            "rating_count": one.rating_count,
            "sla": one.sla,
            "veg": one.veg,
            "badges": list(one.badges),
            "variants": [pack(v) for v in one.variants],
            "merchant_allowed": seller in policy.merchants,
            "category_allowed": one.category in policy.categories,
        }

    # On the mock an alternative is the same product at another shop and says
    # so; live, it is another product at the shop we are already in.
    here = merchant if is_live() else MERCHANT_NAME
    return {
        "product": described(listing, listing.merchant or here),
        "alternatives": [described(alt, alt.merchant or here) for alt in alternatives],
        "comparable": not is_live(),
    }


@app.get("/api/catalog")
def catalog(q: str = "") -> dict:
    """Cross-merchant search, and whether each shop and category is covered.

    Mock only. Swiggy is Instamart alone, so on the live path there is no second
    shop to compare against and the card would be asserting a comparison nobody
    made. `comparable` says which it is rather than leaving the app to guess.
    """
    return {"offers": _offer_rows(q), "live": is_live(), "comparable": not is_live()}


# --- where things get delivered ---------------------------------------------
#
# The third user-owned document, and the one with the sharpest edge on it. A
# mandate that bounds the cap, the shop and the scope is still worth nothing if
# an agent can change the doorstep — ₹1,900 of perfectly ordinary groceries,
# entirely in policy, sent to a stranger.
#
# So the address book is read from the merchant, the *user* picks one, and the
# choice is pushed down to the commerce session and up into the policy in the
# same act. There is no agent tool that reaches either route, for the same
# reason none writes the shopping list.
#
# What travels is the **id**. Never the prose — `basket.Address` has the receipt
# for why.


class AddressChoice(BaseModel):
    address_id: str = Field(min_length=1, description="An id from the user's own address book")


def _address_rows() -> list[dict]:
    """The book, with the policy's answer attached to each row."""
    policy = POLICIES["mdt_demo"]
    try:
        book = MARKETPLACE.addresses()
    except Exception as exc:  # a merchant outage is a 503, not an empty book
        raise HTTPException(503, f"could not read your addresses: {exc}") from exc
    return [
        {
            "address_id": address.address_id,
            "label": address.label,
            "line": address.line,
            "selected": address.address_id == DELIVERY_ID,
            "authorised": address.address_id in policy.delivery_addresses,
        }
        for address in book
    ]


@app.get("/api/addresses")
def all_addresses() -> dict:
    """Every address on the account, and which one orders currently go to."""
    return {"addresses": _address_rows(), "delivery_id": DELIVERY_ID}


@app.put("/api/address")
def choose_address(body: AddressChoice) -> dict:
    """Deliver here from now on. **A user action, and only a user action.**

    Selecting is authorising, and that is honest rather than lax: every row in
    the book is already an address on the user's own account, so there is no
    third party to introduce. What the mandate stops is somebody *else* — the
    agent, or a one-time grant — adding one.

    The new address **replaces** the authorised set rather than joining it. A
    mandate should authorise where you are actually delivering; addresses that
    quietly accumulate are authority nobody remembers granting.
    """
    global DELIVERY_ID

    known = {row["address_id"]: row for row in _address_rows()}
    if body.address_id not in known:
        raise HTTPException(404, "that address is not on your account")

    DELIVERY_ID = body.address_id
    save_delivery()
    # Swiggy holds the delivery address on the session, so the choice has to
    # reach the merchant too — otherwise the cart ships somewhere the policy
    # then refuses, and the refusal names the wrong thing.
    MARKETPLACE.use_address(DELIVERY_ID)
    for mandate_id, policy in list(POLICIES.items()):
        # Grants pin their own basket and its address. Only standing rules move.
        if policy.cart_id is None:
            POLICIES[mandate_id] = replace(policy, delivery_addresses=frozenset({DELIVERY_ID}))

    return {"addresses": _address_rows(), "delivery_id": DELIVERY_ID}


def _list_rows(shopping: ShoppingList) -> dict:
    """The list, priced at the merchant the mandate allows.

    Pricing reads the mock's local catalog. On the live path there is no local
    catalog — a price means a `search_products` call per line, which is a dozen
    round trips to render one screen — so live lists show names and categories
    without prices rather than pretending to a total nobody fetched.
    """
    policy = POLICIES["mdt_demo"]
    seller = next(iter(policy.merchants))
    # Live lists carry no prices by design — a price per line is a
    # `search_products` per line — so an unreachable shop changes nothing here
    # and the `shop` block on `/api/home` is what reports it.
    catalog = {} if is_live() else MARKETPLACE[seller].catalog
    items = [
        {
            "name": name,
            "price_paise": catalog[name].price_paise if name in catalog else None,
            # The user's own classification, not the merchant's. On the live
            # path it is the only one there is.
            "category": shopping.category_of(name)
            or (catalog[name].category if name in catalog else ""),
            "url": f"/m/{seller}/p/{quote(name)}",
            "image_url": catalog[name].image_url if name in catalog else "",
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


def _unstocked(names: list[str]) -> list[str]:
    """Names this shop does not sell.

    Answerable on the mock, which holds its whole catalog in memory. On the live
    path it is one `search_products` per line — a dozen round trips to validate
    one list edit — so it is not asked, and the list accepts the name.

    Nothing is lost by that. An unbuyable line is caught where it costs
    something: `create_cart` reports what it could not resolve, the cart comes
    back short, and the engine rules on the cart that exists rather than the one
    that was asked for.
    """
    if is_live():
        return []
    return [name for name in names if name not in MARKETPLACE[MERCHANT_NAME].catalog]


def _shop_state() -> dict:
    """Which shop the engine is talking to, and whether it is answering.

    On screen because nothing said it, and a truthful "none of those are in
    stock" from a seventeen-item fixture is indistinguishable from a broken
    integration. A whole conversation went into finding that out.

    Decided here rather than in the app for the usual reason: the app cannot see
    `BM_COMMERCE` or whether a token exists, and guessing would be inventing an
    answer about the thing most worth being exact about.
    """
    if not is_live():
        size = len(MARKETPLACE[MERCHANT_NAME].catalog)
        return {
            "backend": "mock",
            "reachable": True,
            "catalogue": f"{size} items",
            "detail": (
                f"A simulated shop of {size} staples — real prices, invented. "
                "Anything outside it will honestly come back unstocked."
            ),
        }
    try:
        MARKETPLACE.addresses()
    except SHOP_DOWN as exc:
        return {
            "backend": "swiggy",
            "reachable": False,
            "catalogue": "unreachable",
            "detail": str(exc),
        }
    return {
        "backend": "swiggy",
        "reachable": True,
        "catalogue": "live",
        "detail": "Live Instamart. Prices, stock and photographs are the real ones.",
    }


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
    # A line the shop does not stock is not a reason to refuse the list.
    #
    # The list is the user's own record of what they want, and refusing to store
    # "Epigamia blueberry yogurt" because our catalog is seventeen items long is
    # the mock's limitation leaking into their document. It already travels back
    # marked `unstocked` on every row, which is the honest way to say it — and
    # the engine still rules on the cart that actually gets built.

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
    categories: dict[str, str] | None = Field(
        default=None,
        description="What kind of thing each line is. The user's call, and the "
        "only classification Swiggy will not give us.",
    )


@app.put("/api/list/{list_id}")
def write_list(list_id: str, body: ListEdit) -> dict:
    """Replace the list. **A user action, and only a user action** — the buyer
    agent holds no tool that reaches this route, because an agent that could
    redefine "my usual groceries" could then order the new definition entirely
    within policy."""
    shopping = LISTS.get(list_id)
    if shopping is None:
        raise HTTPException(404, "no such list")
    # Same as above: what the shop stocks is reported, never enforced, on a
    # document that belongs to the user.
    supplied = body.categories or {}
    # `fees` is allowed by every policy, so letting it be assigned to goods
    # would hand out a category that clears the scope check on anything.
    # Only the commerce adapter mints a fee line.
    smuggled = [name for name, category in supplied.items() if category == FEES]
    if smuggled:
        raise HTTPException(400, f"`{FEES}` is not a category you can assign: {smuggled[0]}")

    kept = {name: supplied.get(name) or shopping.category_of(name) for name in body.item_names}
    LISTS[list_id] = replace(
        shopping,
        item_names=tuple(body.item_names),
        categories={name: category for name, category in kept.items() if category},
    )
    return _list_rows(LISTS[list_id])


@app.get("/m/{merchant}/p/{name}", response_class=HTMLResponse)
def product_page(merchant: str, name: str) -> str:
    """A real product page, so a product link resolves instead of 404ing.

    The injected catalog item gets one too, which is the point: the planted
    instruction is sitting in a product title that the agent reads and the
    shopper never looks at twice.
    """
    item = _product(merchant, name)
    if item is None:
        raise HTTPException(404, "not stocked")
    photo = (
        f"<img src='{escape(item.image_url)}' alt='' width='160' height='160' "
        f"style='object-fit:contain;margin-bottom:1rem'>"
        if item.image_url
        else ""
    )
    return (
        f"<!doctype html><meta charset=utf-8>"
        f"<title>{escape(item.name)} · {escape(merchant)}</title>"
        f"<body style='font:16px/1.5 ui-sans-serif,system-ui;max-width:34rem;"
        f"margin:3rem auto;padding:0 1rem'>"
        f"<p style='color:#8E96C8;text-transform:uppercase;letter-spacing:.08em;"
        f"font-size:.75rem'>{escape(merchant)}</p>"
        f"{photo}"
        f"<h1 style='font-size:1.25rem'>{escape(item.name)}</h1>"
        f"<p style='font-size:1.5rem;font-weight:600'>₹{item.price_paise / 100:,.0f}</p>"
        f"<p style='color:#666'>{escape(item.category or 'uncategorised')}</p>"
    )


def _product(merchant: str, name: str):
    """One product, from whichever backend is running.

    The mock holds a catalog and can be asked directly. Swiggy has no
    by-name lookup, so this costs one `search_products` — acceptable for a page
    a person opened by tapping a link, and not on any path the agent walks.
    """
    if is_live():
        # Guarded here rather than at each caller, because there are two of them
        # and one of them escaped as a bare 500 for exactly that reason.
        try:
            found = MARKETPLACE.search(name)
        except SHOP_DOWN as exc:
            raise shop_down(exc) from exc
        matches = [item for _, item in map(_offer_parts, found)]
        return next((m for m in matches if m.name == name), None) or next(iter(matches), None)
    try:
        return MARKETPLACE[merchant].catalog[name]
    except (UnknownMerchant, UnknownItem, KeyError):
        return None


# --- the home screen ---------------------------------------------------------
#
# The one surface this product could not do without, and the last one built.
#
# Everything else here assumes somebody is present: a thread you type into, a
# card that answers a question you asked. But the whole claim is that **nobody
# is present** — the scheduler proposes at 9am, the engine rules, the ledger
# records, and if you were not in the thread at that moment nothing ever told
# you. An unattended decision had nowhere to land.
#
# So home answers one question before it is asked: *where do I stand?* And the
# server decides what that answer is, for the same reason it decides `off_scope`
# and `merchant_allowed` — whether a thing needs the user is the policy's
# judgement, and a client should not be reimplementing it.

#: A list falling due inside this window is worth saying out loud before it
#: runs. Wider than a day, so an overnight order is announced the evening
#: before rather than at three in the morning.
PREFLIGHT_WINDOW = timedelta(hours=36)

#: How long an order stays *news*. After this the receipt stops being the thing
#: the screen leads with, and the rule goes back to quietly running.
NEWS_WINDOW = timedelta(hours=12)


@dataclass(frozen=True)
class HomeState:
    """What the home card says, and what it offers.

    `actions` are **proposed and never taken** — the same contract the engine
    keeps with the agent, rendered as UI. A refusal offers none, and that
    absence is deliberate: an agent caught misreporting its own basket is not a
    thing to wave through with one tap.
    """

    state: str
    headline: str
    detail: str
    actions: tuple[str, ...] = ()
    decision: dict | None = None
    grant_id: str | None = None
    list_id: str | None = None


def _ledger_view() -> tuple[set[str], list[dict], list[dict]]:
    """One pass over the ledger for everything home needs from it.

    Dismissed keys, every decision oldest-first, and every settlement — which is
    the only record that money actually moved rather than an order being placed.
    """
    dismissed: set[str] = set()
    decisions: list[dict] = []
    settled: list[dict] = []
    for entry in LEDGER.entries():
        payload = entry.payload
        if payload.get("event") == "SETTLED":
            settled.append({**payload, "ts": entry.ts})
        elif payload.get("event") == "SEEN":
            dismissed.add(payload.get("idempotency_key"))
        elif payload.get("event") == "HALTED":
            decisions.append(
                {
                    **payload,
                    "ts": entry.ts,
                    "verdict": Verdict.ESCALATE.value,
                    "idempotency_key": f"halt_{payload.get('cart_id')}",
                    "reasons": [{"code": "delivery.unknown_address", "detail": payload["detail"]}],
                }
            )
        elif payload.get("verdict"):
            decisions.append({**payload, "ts": entry.ts})
    return dismissed, decisions, settled


def _needs_you(decision: dict) -> HomeState:
    """A decision the user has to answer, in the words for *why*.

    Four different shapes, because they want four different answers. Naming the
    wrong one is worse than naming none — the same reason the offers card
    answers merchant and category separately.
    """
    codes = decision.get("reason_code", "")
    rows = decision.get("reasons") or []
    # The *deciding* reason, not all of them run together. Concatenating every
    # detail produced a paragraph — "This cart was already authorised. 2 items
    # outside your scope: Bluetooth earbuds, Phone case. Already 1 order in the
    # last 4 days." — which is three separate problems in one breath. The rest
    # travel as `reasons` and the card lists them; this is the sentence.
    deciding = next(
        (r for r in rows if r.get("verdict") == decision.get("verdict")), rows[0] if rows else {}
    )
    detail = deciding.get("detail") or summary(codes)
    rupees = f"₹{decision.get('total_paise', 0) / 100:,.0f}"

    if "delivery.unknown_address" in codes:
        return HomeState(
            "needs_you",
            "Halted — that is not an address you authorised.",
            f"A {rupees} basket is staged for somewhere your rule does not cover. "
            "Nothing ships until you say so.",
            ("reauthorise", "cancel_basket"),
            decision=decision,
        )
    if decision["verdict"] == Verdict.DENY.value:
        return HomeState(
            "needs_you",
            "Refused, and nothing was charged.",
            detail,
            # No approval path, on purpose.
            ("see_attempt",),
            decision=decision,
        )
    if decision["verdict"] == Verdict.CLARIFY.value:
        return HomeState(
            "needs_you",
            "One line needs an answer.",
            detail,
            ("classify", "approve_once", "leave_out"),
            decision=decision,
        )
    return HomeState(
        "needs_you",
        f"Your call on {rupees}.",
        detail,
        ("approve_once", "drop_flagged", "not_now"),
        decision=decision,
    )


def _home_state(now: datetime | None = None) -> HomeState:
    """Which of the five states the engine is in. A pure read.

    Precedence: something waiting on you, then money already committed, then the
    newest thing that happened, then the next thing due. "Needs you" outranks
    everything because it is the only state where the product is stuck.
    """
    now = now or datetime.now(UTC)
    dismissed, decisions, settlements = _ledger_view()
    latest = next(
        (d for d in reversed(decisions) if d.get("idempotency_key") not in dismissed), None
    )

    if latest and latest["verdict"] != Verdict.ALLOW.value:
        return _needs_you(latest)

    live = next((g for g in GRANTS.values() if g.state(now) == "ready"), None)
    if live is not None:
        return HomeState(
            "grant_live",
            f"Approved — ₹{live.amount_paise / 100:,.0f}, this basket only.",
            "It lapses in fifteen minutes and can be spent once.",
            ("pay", "let_lapse"),
            grant_id=live.grant_id,
        )

    # Money that actually moved outranks an order that was merely placed.
    #
    # `ruled` used to catch this and say "placed while you were away", which is
    # wrong twice over: the reader was standing there paying, and an order is
    # not a payment — a distinction this codebase insists on everywhere else and
    # had no screen for.
    paid = settlements[-1] if settlements else None
    if paid and datetime.fromisoformat(paid["ts"]) >= now - NEWS_WINDOW:
        grant = GRANTS.get(paid.get("grant_id") or "")
        amount = f"₹{grant.amount_paise / 100:,.0f}" if grant else "your order"
        return HomeState(
            "paid",
            f"Paid — {amount}. It is on its way.",
            f"Reference {paid.get('razorpay_payment_id', '—')}. The signature was "
            "checked before this was written down, and the chain covers it.",
            ("view_basket", "verify_chain"),
            grant_id=grant.grant_id if grant else None,
        )

    # An order that just happened outranks one that has not. It is the newer
    # fact, and the reader was not there to see it — which is the whole reason
    # this screen exists.
    if latest and datetime.fromisoformat(latest["ts"]) >= now - NEWS_WINDOW:
        return HomeState(
            "ruled",
            f"Ordered — ₹{latest.get('total_paise', 0) / 100:,.0f}, inside your rule.",
            "Placed while you were away. Every step of it is in the ledger.",
            ("view_basket", "verify_chain"),
            decision=latest,
        )

    due = sorted(
        (s for s in LISTS.values() if s.next_due(now) is not None),
        key=lambda s: s.next_due(now),  # type: ignore[return-value]
    )
    soon = next((s for s in due if s.next_due(now) - now <= PREFLIGHT_WINDOW), None)
    if soon is not None:
        rows = _list_rows(soon)
        when = soon.next_due(now)
        moment = "shortly" if when <= now else when.strftime("%-d %b at %H:%M")
        return HomeState(
            "preflight",
            f"{soon.name} goes out {moment}.",
            f"₹{rows['total_paise'] / 100:,.0f} of your ₹{rows['cap_paise'] / 100:,.0f} cap. "
            "Nothing for you to do.",
            ("pause", "view_basket"),
            list_id=soon.list_id,
        )

    nxt = due[0].next_due(now) if due else None
    return HomeState(
        "at_rest",
        "Your rule is running.",
        (
            f"Next order {nxt.strftime('%-d %b at %H:%M')}."
            if nxt
            else "Nothing scheduled — your lists run only when you ask."
        )
        + " You will hear from it when something crosses a line.",
        ("view_rule", "pause"),
    )


def _rule_block() -> dict:
    """The standing mandate, as bounds rather than prose.

    It had no screen and no route until now, which is a strange gap in a product
    whose whole subject it is.
    """
    policy = POLICIES["mdt_demo"]
    # An unreachable shop must not take the whole screen down: the `shop` block
    # is what reports it, and a home screen that 503s cannot show you why.
    try:
        here = next((a for a in _address_rows() if a["selected"]), None)
    except (HTTPException, *SHOP_DOWN):
        here = None
    return {
        "per_txn_max_paise": policy.per_txn_max_paise,
        "merchants": sorted(policy.merchants),
        # `fees` is allowed by construction and is not a thing anybody chose to
        # buy, so it does not belong in a sentence describing what you allowed.
        "categories": sorted(policy.categories - {FEES}),
        "every_days": policy.window_days,
        "orders_per_window": policy.max_charges_per_window,
        "status": policy.status.value,
        "delivery": {"label": here["label"], "line": here["line"]} if here else None,
    }


@app.get("/api/home")
def home() -> dict:
    """Where do I stand — answered before anybody asks."""
    current = _home_state()
    entries = list(LEDGER.entries())
    try:
        LEDGER.verify()
        intact = True
    except Exception:
        intact = False
    decision = current.decision
    if current.state == "paid" and current.grant_id:
        # From the grant's own snapshot: the basket that was paid for, which is
        # what a receipt is about.
        grant = GRANTS.get(current.grant_id)
        if grant:
            decision = {
                "verdict": Verdict.ALLOW.value,
                "items": grant.items,
                "total_paise": grant.amount_paise,
                "reasons": [],
                "idempotency_key": "",
                "reason_code": "ok.in_policy",
            }
    elif decision and decision.get("cart_id"):
        # The same lines the thread renders, so a basket looks like itself
        # wherever it appears — and so an escalation can show *which* two items
        # rather than only counting them.
        cart = MARKETPLACE.fetch_cart(decision["cart_id"])
        decision = {
            **decision,
            "items": _cart_lines(cart, POLICIES.get(decision.get("mandate_id"))),
        }

    return {
        "rule": _rule_block(),
        "shop": _shop_state(),
        "state": current.state,
        "chip": chip(current.state),
        "headline": current.headline,
        "detail": current.detail,
        "actions": [action(a) for a in current.actions],
        "decision": decision,
        "grant_id": current.grant_id,
        "list_id": current.list_id,
        "lists": [_list_rows(s) for s in LISTS.values()],
        "chain_intact": intact,
        "recent": [
            {
                "ts": e.ts,
                "verdict": e.payload.get("verdict"),
                "summary": summary(e.payload.get("reason_code", "")),
                "total_paise": e.payload.get("total_paise"),
                "event": e.payload.get("event"),
            }
            for e in entries[-6:][::-1]
        ],
    }


class Seen(BaseModel):
    idempotency_key: str = Field(min_length=1)


@app.post("/api/home/seen")
def mark_seen(body: Seen) -> dict:
    """Dismiss what the home card is showing.

    Written to the ledger rather than mutating anything, because this ledger is
    append-only and "the user looked at it" is an event — the same class of
    thing as the decision it dismisses.

    **You cannot dismiss what has not happened.** Idempotency keys are
    `sha256(mandate | window | cart)[:32]` — deterministic, and cart ids are
    predictable on both backends (sequential on the mock, a content hash on
    Swiggy). So the key of a decision that has not been made yet is computable,
    and without this check it could be dismissed *in advance*: the engine would
    still refuse the basket, and the user would never be told it had. Silencing
    the interrupt defeats the escalation as thoroughly as widening the cap
    would, and it is the quieter of the two failures.
    """
    known = {
        entry.payload.get("idempotency_key")
        for entry in LEDGER.entries()
        if entry.payload.get("verdict") or entry.payload.get("event") == "HALTED"
    }
    # A HALTED event carries no key of its own; `_ledger_view` derives one.
    known |= {
        f"halt_{entry.payload.get('cart_id')}"
        for entry in LEDGER.entries()
        if entry.payload.get("event") == "HALTED"
    }
    if body.idempotency_key not in known:
        raise HTTPException(404, "there is no such decision to dismiss")

    LEDGER.append({"event": "SEEN", "idempotency_key": body.idempotency_key})
    return {"seen": body.idempotency_key, "state": _home_state().state}


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
            cart = MARKETPLACE.create_cart(list(shopping.item_names), merchant="instamart")
        except (UnknownMerchant, UnknownItem) as exc:
            out.append({"list_id": list_id, "error": str(exc.args[0])})
            continue
        except SHOP_DOWN as exc:
            # Recorded against the list rather than raised: one unreachable shop
            # should not stop the tick, and a run that could not happen is worth
            # saying so about.
            out.append({"list_id": list_id, "error": str(exc)})
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
