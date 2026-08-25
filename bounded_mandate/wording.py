"""Reason codes, in words a person did not have to learn.

`cap.exceeded` is the right name for a thing the ledger stores and a test
asserts on. It is the wrong thing to show someone who just wanted groceries.
The engine keeps the code; this decides what to call it out loud.

Kept out of `engine.py` on purpose — the engine decides, it does not present —
and kept server-side rather than in the app so the machine name and the human
one cannot drift apart in a client nobody remembered to update.
"""

from __future__ import annotations

TITLES: dict[str, str] = {
    "ok.in_policy": "Within your rule",
    # Layer 1 — the hard bounds.
    "cap.exceeded": "Over your limit",
    "category.not_allowed": "Not what you allowed",
    "category.unknown": "Might not be in scope",
    "merchant.not_allowed": "Shop you didn't allow",
    "delivery.unknown_address": "Address you didn't authorise",
    "frequency.exceeded": "Sooner than agreed",
    # Layer 0 — provenance.
    "provenance.total_mismatch": "The agent misreported the total",
    "provenance.cart_not_found": "No such basket",
    "duplicate.suppressed": "Already ordered",
    # Mandate state.
    "mandate.unknown": "No such rule",
    "mandate.revoked": "Rule was cancelled",
    "mandate.paused": "Rule is paused",
    "mandate.expired": "Rule has run out",
    # Layer 2 and the pattern detector.
    "intent.mismatch": "Doesn't look like your usual order",
    "agent.probing": "This agent keeps trying",
    "semantic.unavailable": "One extra check didn't run",
}


def title(code: str) -> str:
    """A human name for one reason code.

    An unknown code falls back to something readable rather than the raw
    identifier, because a code reaching the screen is a bug in this table and
    the user should not be the one who pays for it.
    """
    if code in TITLES:
        return TITLES[code]
    tail = code.split(".")[-1]
    return tail.replace("_", " ").capitalize()


def summary(codes: str) -> str:
    """The `a+b+c` reason string, as a sentence fragment."""
    titles = [title(code) for code in codes.split("+") if code]
    if not titles:
        return ""
    if len(titles) == 1:
        return titles[0]
    return ", ".join(titles[:-1]) + " and " + titles[-1].lower()
