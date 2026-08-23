"""Policy compiler — plain language in, an enforceable contract out.

    "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"
      -> Policy(per_txn_max_paise=200_000, merchants={"instamart"},
                categories={"groceries"}, max_charges_per_window=1, window_days=4)

This runs **outside the trust boundary**. Its output is reflected back on the
setup card and the user confirms it before it becomes authority, so a mistake
here is caught by a human at setup rather than by the engine at runtime. That
is why there is no retry loop, no self-critique and no second opinion: the
reflect-back card is the validation.

The one rule that does matter: **it may not invent a bound.** An unstated cap
comes back `None` and the surface asks. A guessed ₹2,000 would be authority the
user never granted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from .engine import MandateStatus, Policy

MODEL = "claude-opus-5"

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
            categories=frozenset(self.categories),
            delivery_addresses=delivery_addresses,
            # "every 4 days" is one authorised order per 4-day window. Cadence
            # and frequency ceiling are the same bound seen from two sides.
            max_charges_per_window=1,
            window_days=self.cadence_days,
            status=MandateStatus.ACTIVE,
        )


@dataclass(frozen=True)
class Compiled:
    """What the setup card renders: the draft, and what it still needs to ask."""

    utterance: str
    draft: MandateDraft
    policy: Policy | None

    @property
    def missing(self) -> tuple[str, ...]:
        return self.draft.missing


def compile_mandate(
    utterance: str,
    *,
    mandate_id: str,
    delivery_addresses: frozenset[str],
    client: Any | None = None,
) -> Compiled:
    """Compile one plain-language rule. Pass `client` to stub the model in tests."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = client.messages.parse(
        model=MODEL,
        max_tokens=16_000,
        system=SYSTEM,
        messages=[{"role": "user", "content": utterance}],
        output_format=MandateDraft,
    )
    draft = response.parsed_output
    return Compiled(utterance, draft, draft.to_policy(mandate_id, delivery_addresses))


def _render(compiled: Compiled) -> str:
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
    ]
    if compiled.missing:
        lines += ["", f"  Needs an answer before this can register: {', '.join(compiled.missing)}"]
    else:
        lines += ["", "  Confirm and register."]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - eyeball the real model
    import sys

    said = " ".join(sys.argv[1:]) or (
        "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"
    )
    # Credentials resolve from ANTHROPIC_API_KEY or an `ant auth login` profile.
    print(_render(compile_mandate(said, mandate_id="mdt_demo", delivery_addresses=frozenset())))
