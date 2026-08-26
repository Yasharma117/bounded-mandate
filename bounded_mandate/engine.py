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


def _plural(count: int, noun: str) -> str:
    """ "1 item", not "1 item(s)". These strings are read by a person, and
    `(s)` is the sound a form makes, not a sentence."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# A refused proposal is ordinary — an agent can be wrong. A burst of them is
# not: it is something testing where the edges are. Once that pattern shows,
# nothing under this mandate runs silently until a human has looked, including
# proposals that would otherwise pass cleanly.
PROBE_THRESHOLD = 3
PROBE_WINDOW = timedelta(hours=1)


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
    #: A one-time grant is authority over **one basket**, named here. `None` is
    #: a standing mandate, where the cart is not known when the rule is written
    #: and the other bounds are all there is to judge by.
    #:
    #: Without this, a grant approved for a ₹4,000 air fryer would also cover a
    #: different ₹4,000 basket from the same shop inside the same fifteen
    #: minutes — the substitution the user never looked at.
    cart_id: str | None = None


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

    if policy.cart_id is not None and cart.cart_id != policy.cart_id:
        reasons.append(
            Reason(
                "grant.other_cart",
                Verdict.DENY,
                "This approval covers one specific basket, and this is not it.",
            )
        )

    unknown = [i.name for i in cart.items if not i.category]
    off_scope = [i.name for i in cart.items if i.category and i.category not in policy.categories]
    if off_scope:
        reasons.append(
            Reason(
                "category.not_allowed",
                Verdict.ESCALATE,
                f"{_plural(len(off_scope), 'item')} outside your scope: {', '.join(off_scope)}.",
            )
        )
    # A grant pins the exact basket and the user approved these lines by reading
    # them. An unclassifiable line is what such a grant is *for* — asking "is
    # this in scope?" about a basket the user personally approved is a question
    # with nobody left to answer it.
    if unknown and policy.cart_id is None:
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
                f"Already {_plural(prior_charges, 'order')} in the last "
                f"{_plural(policy.window_days, 'day')}.",
            )
        )

    return reasons


def _semantic_reasons(cart: Cart, policy: Policy, check: SemanticCheck | None) -> list[Reason]:
    """Layer 2 — the model, one-directional.

    Whatever it returns is coerced to ESCALATE. There is no return value that
    approves anything, so injecting the model cannot widen the agent's
    authority; the worst it achieves is tripping a flag, which fails safe.

    If the provider is unreachable the layer is skipped rather than failing the
    proposal: Layer 1 enforces every hard bound on its own, and escalating every
    grocery order during an outage would turn the product into the confirm
    dialog it exists to avoid. But the skip is written to the ledger, because a
    decision made with a layer down must not look fully checked afterwards.
    """
    if check is None:
        return []
    try:
        concerns = check(cart, policy)
    except Exception:
        return [
            Reason(
                "semantic.unavailable",
                Verdict.ALLOW,
                "Semantic check did not run; decided on deterministic policy alone.",
            )
        ]
    return [Reason("intent.mismatch", Verdict.ESCALATE, concern) for concern in concerns]


@dataclass(frozen=True)
class _History:
    """What the ledger already knows about this mandate."""

    charges_in_window: int
    charged_keys: frozenset[str]
    recent_denials: int


def _history(ledger: Ledger, policy: Policy, now: datetime) -> _History:
    """One pass over the ledger for everything the decision needs from it.

    ponytail: full scan per decision. Trivial at demo volume; index by mandate
    id if the ledger ever grows past memory.
    """
    since_window = (now - timedelta(days=policy.window_days)).isoformat()
    since_probe = (now - PROBE_WINDOW).isoformat()
    charges, keys, denials = 0, set(), 0
    for entry in ledger.entries():
        p = entry.payload
        if p.get("mandate_id") != policy.mandate_id:
            continue
        if p.get("verdict") == Verdict.ALLOW.value:
            if entry.ts >= since_window:
                charges += 1
            keys.add(p.get("idempotency_key"))
        elif p.get("verdict") == Verdict.DENY.value and entry.ts >= since_probe:
            denials += 1
    return _History(charges, frozenset(keys), denials)


def _probe_reason(denials: int) -> list[Reason]:
    """A pattern of refusals is itself a finding, and the user is the one who
    needs it. Escalate rather than deny: the proposal in front of us may be
    perfectly fine, and the point is that nobody should take that on trust from
    an agent that has spent the last hour testing the fence."""
    if denials < PROBE_THRESHOLD:
        return []
    return [
        Reason(
            "agent.probing",
            Verdict.ESCALATE,
            f"{denials} refused attempts in the last hour. This agent may be "
            f"compromised — nothing runs on its own until you have looked.",
        )
    ]


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

    history = _history(ledger, policy, now)
    if key in history.charged_keys:
        reasons.append(
            Reason("duplicate.suppressed", Verdict.DENY, "This cart was already authorised.")
        )

    reasons += _mandate_reasons(policy, now)
    reasons += _policy_reasons(cart, policy, history.charges_in_window)
    reasons += _probe_reason(history.recent_denials)

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
