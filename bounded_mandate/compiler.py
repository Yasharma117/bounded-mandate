"""Policy compiler — plain language in, an enforceable contract out.

    "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"
      -> Policy(per_txn_max_paise=200_000, merchants={"instamart"},
                categories={"groceries"}, max_charges_per_window=1, window_days=4)

This runs **outside the trust boundary**. Its output is reflected back on the
setup card and the user confirms it before it becomes authority, so a mistake
here is caught by a human at setup rather than by the engine at runtime. That
is why there is no retry loop and no self-critique: the reflect-back card is
the validation.

The one rule that does matter: **it may not invent a bound.** An unstated cap
comes back `None`, the rule refuses to compile, and the surface asks. A guessed
₹2,000 would be authority the user never granted — and that rule binds the
offline fallback exactly as it binds the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from .categories import with_fees
from .engine import MandateStatus, Policy
from .llm import complete_json

SYSTEM = """You compile a spoken purchasing rule into a machine-readable mandate.

Extract only what the user actually said. This is transcription into a schema,
not advice — you are not deciding what a sensible limit would be.

- Amounts are integer paise. ₹2,000 is 200000. ₹1 is 100.
- A bound the user did not state is null (or an empty list). Never infer one
  from what a typical person would want. An invented limit is authority the
  user never granted, and the whole product rests on not doing that.
- Merchant and category names: lowercase, singular where natural
  ("Instamart" -> "instamart", "Groceries" -> "groceries").
- cadence_days is how often the order should repeat: "every 4 days" -> 4,
  "weekly" -> 7. If the user gave no cadence, null.
"""


class MandateDraft(BaseModel):
    """The model's read of the utterance. Every bound is nullable on purpose."""

    per_txn_max_paise: int | None = Field(
        description="Per-order spend cap in paise; null if unstated"
    )
    merchants: list[str] = Field(description="Merchants the agent may buy from; empty if unstated")
    categories: list[str] = Field(description="What it may buy; empty if unstated")
    cadence_days: int | None = Field(
        description="How often the order repeats, in days; null if unstated"
    )

    @property
    def missing(self) -> tuple[str, ...]:
        """Bounds the user left unsaid. Every field here is one the engine needs."""
        return tuple(f for f in type(self).model_fields if not getattr(self, f))

    def to_policy(self, mandate_id: str, delivery_addresses: frozenset[str]) -> Policy | None:
        """The compiled contract, or `None` if the user left a bound unsaid."""
        if self.missing:
            return None
        return Policy(
            mandate_id=mandate_id,
            per_txn_max_paise=self.per_txn_max_paise,
            merchants=frozenset(self.merchants),
            categories=with_fees(self.categories),
            delivery_addresses=delivery_addresses,
            # "every 4 days" is one authorised order per 4-day window. Cadence
            # and frequency ceiling are the same bound seen from two sides.
            max_charges_per_window=1,
            window_days=self.cadence_days,
            status=MandateStatus.ACTIVE,
        )


# --- the offline fallback ----------------------------------------------------
#
# The recorded demo is a live walkthrough, so a provider hiccup must not be able
# to break it. This handles the shapes a spoken rule actually takes; anything it
# cannot read comes back as a missing bound, which is the correct answer anyway.
# It never guesses — same rule as the model.

_AMOUNT = re.compile(r"(?:₹|\brs\.?|\binr)\s*([\d,]+)|\bunder\s+([\d,]+)", re.I)
_EVERY_N_DAYS = re.compile(r"\bevery\s+(\d+)\s*days?\b", re.I)
_WORD_CADENCE = {"daily": 1, "every day": 1, "weekly": 7, "every week": 7, "fortnightly": 14}
_MERCHANTS = ("instamart", "blinkit", "zepto", "bigbasket", "dmart", "swiggy")
_CATEGORIES = {
    "groceries": "groceries",
    "grocery": "groceries",
    "household": "household",
    "essentials": "essentials",
    "medicines": "medicines",
    "snacks": "snacks",
    "beverages": "beverages",
}


def _offline_draft(utterance: str) -> MandateDraft:
    said = utterance.casefold()

    amount = _AMOUNT.search(utterance)
    rupees = next((g for g in amount.groups() if g), None) if amount else None

    cadence = _EVERY_N_DAYS.search(said)
    days = int(cadence.group(1)) if cadence else None
    if days is None:
        days = next((n for word, n in _WORD_CADENCE.items() if word in said), None)

    return MandateDraft(
        per_txn_max_paise=int(rupees.replace(",", "")) * 100 if rupees else None,
        merchants=[m for m in _MERCHANTS if m in said],
        categories=sorted({v for k, v in _CATEGORIES.items() if k in said}),
        cadence_days=days,
    )


@dataclass(frozen=True)
class Compiled:
    """What the setup card renders: the draft, and what it still needs to ask."""

    utterance: str
    draft: MandateDraft
    policy: Policy | None
    source: str  # "model" or "fallback" — never hidden from the operator

    @property
    def missing(self) -> tuple[str, ...]:
        return self.draft.missing


def compile_mandate(
    utterance: str,
    *,
    mandate_id: str,
    delivery_addresses: frozenset[str],
    client: Any | None = None,
    model: str | None = None,
) -> Compiled:
    """Compile one plain-language rule.

    Falls back to the offline parser if the provider is unreachable or answers
    with something unusable, and says so in `source`. Pass `client` to stub the
    model in tests.
    """
    try:
        payload = complete_json(
            SYSTEM,
            utterance,
            MandateDraft.model_json_schema(),
            client=client,
            model=model,
        )
        draft, source = MandateDraft.model_validate(payload), "model"
    except Exception:
        draft, source = _offline_draft(utterance), "fallback"

    return Compiled(utterance, draft, draft.to_policy(mandate_id, delivery_addresses), source)


def render(compiled: Compiled) -> str:
    """The reflect-back card, as text. Scene 1 of the demo prints this."""
    d = compiled.draft
    rupees = "—" if d.per_txn_max_paise is None else f"₹{d.per_txn_max_paise / 100:,.0f}"
    lines = [
        f'  "{compiled.utterance}"',
        "",
        f"  Spend limit   {rupees} per order",
        f"  Cadence       {'—' if d.cadence_days is None else f'every {d.cadence_days} days'}",
        f"  Merchant      {', '.join(d.merchants) or '—'}",
        f"  Scope         {', '.join(d.categories) or '—'}",
        "",
    ]
    if compiled.missing:
        lines.append(f"  Needs an answer before this can register: {', '.join(compiled.missing)}")
    else:
        lines.append("  Confirm and register.")
    lines.append(f"  [compiled by {compiled.source}]")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - eyeball a real provider call
    import sys

    from .llm import BASE_URL, MODEL

    said = " ".join(sys.argv[1:]) or (
        "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"
    )
    print(f"  {MODEL} @ {BASE_URL}\n")
    print(render(compile_mandate(said, mandate_id="mdt_demo", delivery_addresses=frozenset())))
