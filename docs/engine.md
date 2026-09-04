# The engine

Three layers, four verdicts, and a reason code for every one of them. `decide()` in `bounded_mandate/engine.py` runs the checks in the order described here.

[← back to the README](../README.md)

---

## Decision model

Every proposal returns exactly one verdict plus machine-readable reason codes —
never a bare boolean.

| Layer | What it does | Can it approve? |
|---|---|---|
| **0 · Proposal integrity** | fetch the canonical cart by id; compare against what the agent claimed | no |
| **1 · Hard policy** | deterministic checks: mandate state, merchant, category, cap, delivery, frequency | **yes — only this** |
| **2 · Semantic safety** | the model, one-directional; catches what the rules would wrongly wave through | no |

Layer 2 is backed by NIM (see below) and returns *concerns*, which the engine
coerces to `ESCALATE`. There is no value it can return that approves anything —
which is what makes a compromised Layer 2 harmless rather than catastrophic.


### A pattern of refusals is itself a finding

One refused proposal is ordinary; an agent can be wrong. Several in an hour is
not — it is something testing where the edges are. Past `PROBE_THRESHOLD`
refusals in an hour, `agent.probing` attaches to every decision under that
mandate.

The consequence is the part that matters. It escalates rather than denies,
**including proposals that would otherwise pass cleanly**:

```text
claimed 1345000 -> DENY  provenance.total_mismatch+category.not_allowed+cap.exceeded
claimed 1000000 -> DENY  ...
claimed  500000 -> DENY  ...
claimed  100000 -> DENY  ...+agent.probing

then the same agent submits ₹1,850 of ordinary groceries, all in policy:
                 -> ESCALATE  agent.probing
```

The basket in front of you may be perfectly fine. The point is that nobody
should take that on trust from an agent that has spent the last hour testing
the fence. Silent execution is a privilege extended to a well-behaved agent,
and it is withdrawable.

Escalations are deliberately **not** counted. An over-cap basket is a boundary
being reached, not an attack, and treating ordinary friction as evidence of
compromise would make the signal useless.


### When Layer 2 is down

The model is the one part of the engine that can be unreachable. The choice
there is a real one, so it is written down rather than left to whatever the
code happens to do.

**Fail open, and record it.** A provider outage skips Layer 2 rather than
failing the proposal, because Layer 1 enforces every hard bound on its own and
escalating every grocery order during an outage would turn the product into the
confirm dialog it exists to avoid. But the skip is written to the ledger as
`semantic.unavailable`, so a decision made with a layer down never looks fully
checked afterwards.

`semantic.unavailable` carries no severity of its own — it cannot change a
verdict, only annotate one. A cap breach during an outage still escalates, and
the ledger reads `cap.exceeded+semantic.unavailable`: both facts, neither
hidden.

The tradeoff, stated plainly: an attacker who can deny the provider can disable
Layer 2. What they cannot do is widen authority, because Layer 1 is
deterministic and still holds every bound. The degradation is real and bounded.

Checks deliberately do **not** short-circuit. A proposal collects every reason
it trips and the verdict is the most severe of them, because the escalation
surface is meant to show *"₹400 over your cap"* **and** *"2 items aren't
groceries"* on one screen.


### Verdicts

| Verdict | Meaning |
|---|---|
| `ALLOW` | deterministic and inside policy — clear to charge |
| `CLARIFY` | not enough information to evaluate safely — ask, don't guess |
| `ESCALATE` | a valid proposal that needs explicit human authorization |
| `DENY` | prohibited — blocked and logged |

`CLARIFY` and `ESCALATE` are different acts. *"I can't tell if protein powder is
groceries"* is ambiguity; *"this is ₹400 over your cap"* is a boundary
violation. Different reason codes, different UX. Severity orders
`ALLOW < CLARIFY < ESCALATE < DENY`.


### Reason codes

| Code | Verdict | Trips when |
|---|---|---|
| `ok.in_policy` | `ALLOW` | nothing tripped |
| `provenance.total_mismatch` | `DENY` | the agent's claimed total ≠ the real cart |
| `provenance.cart_not_found` | `DENY` | no such cart at the merchant |
| `mandate.unknown` | `DENY` | the engine holds no such mandate |
| `mandate.revoked` / `mandate.paused` / `mandate.expired` | `DENY` | mandate is not live |
| `duplicate.suppressed` | `DENY` | this cart was already authorized in this window |
| `merchant.not_allowed` | `ESCALATE` | merchant off the allowlist |
| `category.not_allowed` | `ESCALATE` | items outside the authorized scope |
| `category.unknown` | `CLARIFY` | the merchant could not classify an item |
| `cap.exceeded` | `ESCALATE` | total over the per-transaction limit |
| `delivery.unknown_address` | `ESCALATE` | shipping to an unauthorized address |
| `frequency.exceeded` | `ESCALATE` | Nth charge this window over the limit |
| `intent.mismatch` | `ESCALATE` | Layer 2 raised a concern |
| `agent.probing` | `ESCALATE` | a burst of refusals — the agent looks compromised |
| `semantic.unavailable` | *(none)* | Layer 2 could not run; decided on Layer 1 alone |

Merchant, category and delivery are separate policy dimensions on purpose.
*"Instamart only"*, *"groceries only"* and *"to my home"* are three different
constraints — an agent that cannot beat the cap can still ship ₹1,900 of
perfectly ordinary groceries to a stranger's address.


## Audit ledger

Append-only JSONL. Every entry carries the SHA-256 of the entry before it, so
"append-only, replayable" is a property you can verify rather than a convention
you trust — `Ledger.verify()` raises `ChainBroken` if any past entry was edited,
reordered or removed. Every decision path writes exactly one entry.
