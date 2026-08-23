"""Bounded Mandate — the authorization engine.

The agent proposes; this decides. Two structural properties do the load-bearing
work, and both are enforced by the shape of the code rather than by discipline:

1. **The policy is read from the engine's own store**, keyed by the mandate id.
   A proposal cannot carry, hint at, or widen the policy it is judged against.
2. **The cart is fetched from the commerce adapter by id.** The agent supplies a
   reference, never a payload it could lie about. Its self-reported total is
   used for exactly one thing: catching the lie.

Nothing in this module talks to a payment rail. A verdict is a value;
settlement is somebody else's job.

Money is integer paise throughout. Never floats.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .ledger import Ledger


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"
    DENY = "DENY"


# A proposal collects every reason it trips and the verdict is the most severe
# of them. Checks deliberately do not short-circuit: the escalation surface is
# meant to show "₹400 over your cap" *and* "2 items aren't groceries" together.
_SEVERITY = {Verdict.ALLOW: 0, Verdict.CLARIFY: 1, Verdict.ESCALATE: 2, Verdict.DENY: 3}


class MandateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class Reason:
    """One machine-readable finding. Never a bare boolean."""

    code: str
    verdict: Verdict
    detail: str


@dataclass(frozen=True)
class Policy:
    """A compiled mandate: what the user authorised, in enforceable form.

    Merchant, category and delivery are separate dimensions on purpose.
    "Instamart only", "groceries only" and "to my home" are three different
    constraints, and an agent that cannot beat the cap can still ship ₹1,900 of
    perfectly ordinary groceries to a stranger's address.
    """

    mandate_id: str
    per_txn_max_paise: int
    merchants: frozenset[str]
    categories: frozenset[str]
    delivery_addresses: frozenset[str]
    max_charges_per_window: int
    window_days: int
    status: MandateStatus = MandateStatus.ACTIVE
    expires_at: datetime | None = None


@dataclass(frozen=True)
class CartItem:
    name: str
    price_paise: int
    category: str = ""  # "" means the merchant could not classify it -> CLARIFY


@dataclass(frozen=True)
class Cart:
    """The canonical cart, as the merchant holds it. The agent never authors one."""

    cart_id: str
    merchant: str
    items: tuple[CartItem, ...]
    delivery_address: str

    @property
    def total_paise(self) -> int:
        # Derived, so a merchant cannot misreport a total either.
        return sum(item.price_paise for item in self.items)


@dataclass(frozen=True)
class Proposal:
    """What the agent is allowed to say.

    A cart *reference* and a claimed total. Any other field an injected agent
    invents — a raised cap, an extra allowlist entry — has nowhere to land,
    because this is the whole vocabulary.
    """

    mandate_id: str
    cart_id: str
    claimed_total_paise: int


class CommerceAdapter(Protocol):
    """The merchant seam. MCP, REST or a mock — the engine cannot tell."""

    def fetch_cart(self, cart_id: str) -> Cart | None: ...


# Layer 2. Returns human-readable concerns; each becomes one ESCALATE reason.
# It can raise suspicion and nothing else — see `_semantic_reasons`.
SemanticCheck = Callable[[Cart, Policy], list[str]]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reasons: tuple[Reason, ...]
    mandate_id: str
    cart_id: str
    total_paise: int
    idempotency_key: str

    @property
    def reason_code(self) -> str:
        """The ledger's reason column, e.g. `cap.exceeded+intent.mismatch`."""
        return "+".join(r.code for r in self.reasons) or "ok.in_policy"


def idempotency_key(policy: Policy, cart_id: str, now: datetime) -> str:
    """`hash(policy, window, cart)` — the same cart in the same window is one charge."""
    window = int(now.timestamp()) // (policy.window_days * 86_400)
    seed = f"{policy.mandate_id}|{window}|{cart_id}"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def _mandate_reasons(policy: Policy, now: datetime) -> list[Reason]:
    if policy.status is MandateStatus.REVOKED:
        return [Reason("mandate.revoked", Verdict.DENY, "This mandate was revoked.")]
    if policy.status is MandateStatus.PAUSED:
        return [Reason("mandate.paused", Verdict.DENY, "This mandate is paused.")]
    if policy.expires_at is not None and policy.expires_at <= now:
        return [Reason("mandate.expired", Verdict.DENY, "This mandate has expired.")]
    return []


def _policy_reasons(cart: Cart, policy: Policy, prior_charges: int) -> list[Reason]:
    """Layer 1 — deterministic hard policy. The only layer that can approve."""
    reasons: list[Reason] = []

    if cart.merchant not in policy.merchants:
        reasons.append(
            Reason(
                "merchant.not_allowed", Verdict.ESCALATE, f"{cart.merchant} is not on your list."
            )
        )

    unknown = [i.name for i in cart.items if not i.category]
    off_scope = [i.name for i in cart.items if i.category and i.category not in policy.categories]
    if off_scope:
        reasons.append(
            Reason(
                "category.not_allowed",
                Verdict.ESCALATE,
                f"{len(off_scope)} item(s) outside your scope: {', '.join(off_scope)}.",
            )
        )
    if unknown:
        reasons.append(
            Reason(
                "category.unknown",
                Verdict.CLARIFY,
                f"Not sure these are in scope: {', '.join(unknown)}.",
            )
        )

    if cart.total_paise > policy.per_txn_max_paise:
        over = cart.total_paise - policy.per_txn_max_paise
        reasons.append(
            Reason("cap.exceeded", Verdict.ESCALATE, f"₹{over / 100:,.0f} over your cap.")
        )

    if cart.delivery_address not in policy.delivery_addresses:
        reasons.append(
            Reason(
                "delivery.unknown_address",
                Verdict.ESCALATE,
                "Shipping to an address you have not authorised.",
            )
        )

    if prior_charges >= policy.max_charges_per_window:
        reasons.append(
            Reason(
                "frequency.exceeded",
                Verdict.ESCALATE,
                f"Already {prior_charges} order(s) in the last {policy.window_days} days.",
            )
        )

    return reasons


def _semantic_reasons(cart: Cart, policy: Policy, check: SemanticCheck | None) -> list[Reason]:
    """Layer 2 — the model, one-directional.

    Whatever it returns is coerced to ESCALATE. There is no return value that
    approves anything, so injecting the model cannot widen the agent's
    authority; the worst it achieves is tripping a flag, which fails safe.
    """
    if check is None:
        return []
    return [Reason("intent.mismatch", Verdict.ESCALATE, concern) for concern in check(cart, policy)]


def _prior_charges(ledger: Ledger, policy: Policy, now: datetime) -> tuple[int, set[str]]:
    """Authorisations already granted under this mandate inside the window.

    ponytail: full scan of the ledger per decision. Trivial at demo volume;
    index by mandate id if the ledger ever grows past memory.
    """
    since = (now - timedelta(days=policy.window_days)).isoformat()
    count, keys = 0, set()
    for entry in ledger.entries():
        p = entry.payload
        if p.get("mandate_id") != policy.mandate_id or p.get("verdict") != Verdict.ALLOW.value:
            continue
        if entry.ts >= since:
            count += 1
        keys.add(p.get("idempotency_key"))
    return count, keys


def decide(
    proposal: Proposal,
    *,
    policies: dict[str, Policy],
    adapter: CommerceAdapter,
    ledger: Ledger,
    semantic_check: SemanticCheck | None = None,
    now: datetime | None = None,
) -> Decision:
    """Evaluate one proposal and record the outcome. Every path writes to the ledger."""
    now = now or datetime.now(UTC)

    # The policy comes from here and nowhere else.
    policy = policies.get(proposal.mandate_id)
    if policy is None:
        return _record(
            ledger,
            Decision(
                Verdict.DENY,
                (Reason("mandate.unknown", Verdict.DENY, "No such mandate."),),
                proposal.mandate_id,
                proposal.cart_id,
                0,
                "",
            ),
            now,
        )

    # Layer 0 — proposal integrity. Fetch the real cart; never trust the payload.
    cart = adapter.fetch_cart(proposal.cart_id)
    if cart is None:
        return _record(
            ledger,
            Decision(
                Verdict.DENY,
                (Reason("provenance.cart_not_found", Verdict.DENY, "No such cart."),),
                policy.mandate_id,
                proposal.cart_id,
                0,
                "",
            ),
            now,
        )

    key = idempotency_key(policy, cart.cart_id, now)
    reasons: list[Reason] = []

    if proposal.claimed_total_paise != cart.total_paise:
        reasons.append(
            Reason(
                "provenance.total_mismatch",
                Verdict.DENY,
                f"Agent claimed ₹{proposal.claimed_total_paise / 100:,.0f}, "
                f"the real cart is ₹{cart.total_paise / 100:,.0f}.",
            )
        )

    prior_charges, charged_keys = _prior_charges(ledger, policy, now)
    if key in charged_keys:
        reasons.append(
            Reason("duplicate.suppressed", Verdict.DENY, "This cart was already authorised.")
        )

    reasons += _mandate_reasons(policy, now)
    reasons += _policy_reasons(cart, policy, prior_charges)

    # The model runs only when the rules would otherwise wave this through, and
    # only to narrow. Skipping it on an already-fatal proposal saves a call and
    # changes no outcome.
    if not any(r.verdict is Verdict.DENY for r in reasons):
        reasons += _semantic_reasons(cart, policy, semantic_check)

    verdict = max((r.verdict for r in reasons), key=lambda v: _SEVERITY[v], default=Verdict.ALLOW)
    return _record(
        ledger,
        Decision(verdict, tuple(reasons), policy.mandate_id, cart.cart_id, cart.total_paise, key),
        now,
    )


def _record(ledger: Ledger, decision: Decision, now: datetime) -> Decision:
    ledger.append(
        {
            "mandate_id": decision.mandate_id,
            "cart_id": decision.cart_id,
            "verdict": decision.verdict.value,
            "reason_code": decision.reason_code,
            "reasons": [
                {"code": r.code, "verdict": r.verdict.value, "detail": r.detail}
                for r in decision.reasons
            ],
            "total_paise": decision.total_paise,
            "idempotency_key": decision.idempotency_key,
        },
        now=now,
    )
    return decision
