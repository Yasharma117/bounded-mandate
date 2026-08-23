# Bounded Mandate

**An authorization layer between an autonomous buying agent and money.**

The agent proposes. It cannot authorize itself.

Built for the Razorpay AI Buildathon, Track 01 (AI Growth & Agentic Commerce).

---

## The problem

A shopping agent with a confirm button makes its own authorization layer
redundant — if a human taps *yes* on every purchase, the human **is** the layer.

Signed-mandate schemes (Google's AP2, NPCI's proposed UAP) specify the
credential *format*: what an agent was authorized to do. What they don't ship is
the **evaluator** — the runtime that holds cross-purchase state, verifies the
cart is what the agent claims it is, and decides whether a given proposal is
authorized under a mandate the user set once.

That's this.

## The two structural properties

Everything else is detail. These two are enforced by the shape of the code, not
by discipline:

**1. The policy is read from the engine's own store.** A proposal names a
mandate id and nothing more. It cannot carry, hint at, or widen the policy it is
judged against. An injected agent that appends `{"per_txn_max_paise": 9999999}`
to its proposal is writing into a field that does not exist.

**2. The cart is fetched from the merchant, never accepted from the agent.**
The agent supplies a *reference*. The engine fetches the canonical cart by id
and evaluates that. An agent reporting `₹1,850` over a cart that really holds
₹1,850 of groceries plus a hidden ₹15,000 smartwatch passes every downstream
check on a fiction — unless somebody fetches the real cart. Layer 0 does.

A third property falls out of the first two: **the model can only escalate,
never authorize.** Layer 2 (semantic safety) returns concerns, and concerns are
coerced to `ESCALATE`. There is no return value that approves anything. A fully
compromised Layer 2 cannot widen the agent's authority; the worst it achieves is
tripping a flag, which fails safe.

## Policy compiler

Plain language in, an enforceable contract out.

```
"Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"

  Spend limit   ₹2,000 per order
  Cadence       every 4 days
  Merchant      instamart
  Scope         groceries
```

The compiler runs **outside the trust boundary**. Its output is reflected back
on the setup card and the user confirms it before it becomes authority, so a
mistake here is caught by a human at setup rather than by the engine at
runtime — which is why there is no retry loop and no self-critique. The
reflect-back *is* the validation.

The one rule that matters: **it may not invent a bound.** An unstated cap comes
back `None`, the rule does not compile, and the surface asks. A guessed ₹2,000
would be authority the user never granted. Delivery addresses are deliberately
not extractable — they come from the account, never from a sentence.

`"every 4 days"` compiles to `max_charges_per_window=1, window_days=4`. Cadence
and frequency ceiling are the same bound seen from two sides.

## Decision model

Every proposal returns exactly one verdict plus machine-readable reason codes —
never a bare boolean.

| Layer | What it does | Can it approve? |
|---|---|---|
| **0 · Proposal integrity** | fetch the canonical cart by id; compare against what the agent claimed | no |
| **1 · Hard policy** | deterministic checks: mandate state, merchant, category, cap, delivery, frequency | **yes — only this** |
| **2 · Semantic safety** | the model, one-directional; catches what the rules would wrongly wave through | no |

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

Merchant, category and delivery are separate policy dimensions on purpose.
*"Instamart only"*, *"groceries only"* and *"to my home"* are three different
constraints — an agent that cannot beat the cap can still ship ₹1,900 of
perfectly ordinary groceries to a stranger's address.

## Audit ledger

Append-only JSONL. Every entry carries the SHA-256 of the entry before it, so
"append-only, replayable" is a property you can verify rather than a convention
you trust — `Ledger.verify()` raises `ChainBroken` if any past entry was edited,
reordered or removed. Every decision path writes exactly one entry.

## Running it

```bash
uv sync
uv run pytest -q
```

The test suite makes no network calls and needs no API key — the model client is
injected, and stubbed in tests. To compile a rule against the live model:

```bash
uv run python -m bounded_mandate.compiler "groceries from Instamart every 4 days, under ₹2,000"
```

That needs Anthropic credentials (`ANTHROPIC_API_KEY` or an `ant auth login`
profile) and API credit.

## Status

Day 3 of 14. **Phase 1 (engine core) — built.** Layer 0 provenance, Layer 1
hard policy, Layer 2 one-directional hook, four verdicts, idempotency, the
hash-chained ledger and the policy compiler. 28 tests, no network.

**Not yet verified:** the compiler has never been run against the live model —
the account is out of API credit. Its schema and its refusal to invent a bound
are unit-tested against a stub; what remains unproven is the model's own
extraction accuracy, notably rupees-to-paise.

Next: Razorpay test-mode settlement (needs no model, so it is not blocked),
then the buyer agent over a mock merchant.

## Honest seams

Stated here because they will be stated in the demo video too:

- **Razorpay test mode is the only real external integration.** The commerce
  leg is a local mock merchant behind an adapter interface.
- **The Razorpay charge and the commerce order are unrelated money flows.** The
  charge debits a test mandate on our own merchant account. It does not pay for
  any goods, and no goods change hands. Bounded Mandate is the *payer-side
  authorization*; the single arrow in the architecture diagram is not allowed to
  imply the two legs are one.
- Aggregate rolling-window spend limits, atomic reserve-then-commit for
  concurrent proposals, and the full ledger state machine are **specified but
  not built**. They are documented as such rather than implied as working.

## Deliberate simplifications

Marked in-source with `ponytail:` comments naming the ceiling and the upgrade
path. Currently: the ledger is single-process with no file locking, and the
frequency check does a full ledger scan per decision. Both are fine at demo
volume and both have an obvious fix when they aren't.

## Licence

MIT — see [LICENSE](LICENSE).
