"""Layer 2 — semantic safety, backed by a model.

This exists to catch what the deterministic rules would wrongly wave through:
a basket that is technically in-category and under cap but is plainly not the
weekly grocery run the user described.

It is **one-directional by construction**. This module returns concerns, and
concerns become `ESCALATE`. There is no value it can return that approves
anything, which is why a fully compromised Layer 2 cannot widen the agent's
authority — the worst it achieves is tripping a flag, and a flag fails safe.

It also does not swallow its own failures. When the provider is unreachable the
check raises, and the engine records that the layer did not run. Degrading
quietly would put a decision in the ledger that looks fully checked when it
wasn't.
"""

from __future__ import annotations

from typing import Any

from .engine import Cart, Policy
from .llm import complete_json

MAX_CONCERNS = 3

SCHEMA = {
    "type": "object",
    "properties": {
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Short, specific reasons a human should look at this basket. Empty if none."
            ),
        }
    },
    "required": ["concerns"],
}

SYSTEM = """You review a shopping basket an autonomous agent assembled against the
rule its user set. You are a safety check, not a shopper.

You can raise concerns. You cannot approve anything — a separate deterministic
layer decides that, and it has already run. Saying "looks fine" changes nothing,
so do not pad. Return an empty list when nothing stands out.

Raise a concern only for something a reasonable person would want to see before
the money moved. For example: the basket does not resemble what the user
described, an item is wildly out of proportion to the rest, quantities look like
a slip, or the basket looks assembled to reach a limit rather than to meet a
need.

Do not re-check the caps, the merchant, the category list or the delivery
address. Those are enforced exactly, elsewhere, and repeating them here only
adds noise.

Each concern is one short sentence a user would understand. No preamble."""


def _describe(cart: Cart, policy: Policy) -> str:
    items = "\n".join(
        f"  - {i.name} — ₹{i.price_paise / 100:,.0f} [{i.category or '?'}]" for i in cart.items
    )
    return (
        f"The user authorised: {', '.join(sorted(policy.categories))} "
        f"from {', '.join(sorted(policy.merchants))}, "
        f"up to ₹{policy.per_txn_max_paise / 100:,.0f} per order.\n\n"
        f"The agent assembled, at {cart.merchant}, "
        f"totalling ₹{cart.total_paise / 100:,.0f}:\n{items}"
    )


def llm_semantic_check(*, client: Any | None = None, model: str | None = None):
    """Build the Layer 2 callable the engine takes. Raises if the provider fails."""

    def check(cart: Cart, policy: Policy) -> list[str]:
        payload = complete_json(SYSTEM, _describe(cart, policy), SCHEMA, client=client, model=model)
        concerns = payload.get("concerns") or []
        return [str(c).strip() for c in concerns if str(c).strip()][:MAX_CONCERNS]

    return check
