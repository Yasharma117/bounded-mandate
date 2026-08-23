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

## The model

Provider is **NVIDIA NIM** behind its OpenAI-compatible endpoint. The whole
binding is two environment variables, so swapping model — or swapping provider
to anything that speaks the OpenAI shape — is config, not code:

```bash
export NVIDIA_API_KEY=nvapi-...
export BM_LLM_MODEL=nvidia/nemotron-3-super-120b-a12b   # the default
export BM_LLM_BASE_URL=https://integrate.api.nvidia.com/v1
```

**Why that default.** The task is easy; the risk is availability, not
capability. Output is constrained with NIM's `guided_json`, which enforces the
schema at the decoding level (xgrammar), so "clean JSON" stops being a
model-selection criterion — which leaves latency and rate-limit exposure.
Nemotron 3 Super is MoE with ~12B active, and it is NVIDIA's own model on
NVIDIA's own stack, the least likely thing to be cold or deprioritised on a
free tier. `deepseek-ai/deepseek-v4-pro` and `z-ai/glm-5.2` are the alternates
if the buyer agent needs stronger tool-calling.

Nemotron 3 Super is a **hybrid** reasoner and thinks by default, so thinking is
turned off explicitly via `chat_template_kwargs`. Measured on the four-field
extraction: **8.3s with thinking, 0.8s without, identical output.** Neither job
here benefits from a reasoning trace, and a live demo feels every second.
(`_json_object` strips one anyway if some other model emits it — cheap
insurance, not a plan.)

Retries are the SDK's: it already backs off on 429 and 5xx, so the client just
raises `max_retries` above the default rather than owning a retry loop.

### The offline fallback

The recorded demo is a live walkthrough, so a provider hiccup must not be able
to break it. If NIM is unreachable, rate-limited, or answers with something
unusable, the compiler falls back to a deterministic parser that reads the
shapes a spoken rule actually takes — ₹ amounts, `every N days`, `weekly`,
known merchants and categories.

Two properties keep this honest:

- **It never guesses either.** Anything the parser cannot read comes back as a
  missing bound, exactly as it would from the model. The no-invented-authority
  rule binds both paths.
- **It is never hidden.** `Compiled.source` is `"model"` or `"fallback"`, and
  the reflect-back card prints `[compiled by fallback]`. A silent fallback
  during a demo would be a lie told to the room.

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
| `semantic.unavailable` | *(none)* | Layer 2 could not run; decided on Layer 1 alone |

Merchant, category and delivery are separate policy dimensions on purpose.
*"Instamart only"*, *"groceries only"* and *"to my home"* are three different
constraints — an agent that cannot beat the cap can still ship ₹1,900 of
perfectly ordinary groceries to a stranger's address.

## Settlement: two legs, not one

Razorpay is reached only from `razorpay_gateway.py`, the single module holding
the key secret. The agent cannot reach it, and neither can anything the agent
influences.

The two legs are different shapes, and conflating them would collapse the whole
thesis into a confirm dialog:

| | Registration | Charging |
|---|---|---|
| Who is present | the user | nobody |
| How often | once | every order |
| Mechanism | Standard Checkout (`checkout.js`) | server-side token debit |
| Amount | ₹1 authorisation | the actual basket |
| RBI | one-time AFA | in-limit debit, no AFA |

**Registration** is the only part exposed over HTTP, because it is the only part
a human participates in. `POST /api/mandate/order` creates the ₹1 UPI Autopay
authorisation order carrying the token object; the page opens Razorpay's modal;
`POST /api/mandate/verify` checks the HMAC-SHA256 signature over
`order_id|payment_id` before anything is considered registered. A forged
callback registers nothing.

**Charging** has no HTTP route at all, and a test asserts that none exists. The
engine debits an authorised token with nobody watching — that *is* the product.

The key secret never leaves the server process. The page receives `key_id`,
which is public by design, from the order response rather than from a
build-time environment variable, so there is no frontend env prefix to leak.

### Verified against the live test API

| Fact | Result |
|---|---|
| UPI Autopay recurring enabled on the test account | **yes** — no support ticket needed |
| `frequency: "as_presented"` | accepted |
| `token.type: "single_block_multiple_debit"` | accepted |
| `max_amount` ceiling for this MCC | exactly ₹1,00,000 (`10000000` paise) |
| Token object actually validated | yes — a bad `frequency` and an over-ceiling `max_amount` are both rejected server-side |

### Running it

```bash
set -a; . ./.env; set +a
uv run uvicorn bounded_mandate.web:app --reload
```

Then open `http://127.0.0.1:8000`, edit the rule, press **Read it back** to see
it compiled, and **Confirm and register** to open Razorpay's modal in test mode.

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
injected and stubbed, and the fallback path is deterministic by construction.
To compile a rule against the live provider:

```bash
export NVIDIA_API_KEY=nvapi-...
uv run python -m bounded_mandate.compiler "groceries from Instamart every 4 days, under ₹2,000"
```

With no key set that command still works — it prints the same card marked
`[compiled by fallback]`.

There is also a live smoke suite, skipped unless a key is present, because the
unit tests stub the model and therefore prove the wiring while proving nothing
about the model:

```bash
NVIDIA_API_KEY=... uv run pytest tests/test_live.py -v
```

## Status

Day 3 of 14. **Phase 1 (engine core) — built.** Layer 0 provenance, Layer 1
hard policy, Layer 2 one-directional hook, four verdicts, idempotency, the
hash-chained ledger, the policy compiler with its offline fallback, the
model-backed Layer 2, the mock merchant, and the Razorpay registration leg
(order + Standard Checkout + signature verification). 84 tests, no network.

**Verified live against NIM.** The compiler extracts ₹2,000 as `200000` paise,
and refuses to invent a bound from *"whenever we run low"* or *"buy whatever you
think I need"*. Layer 2 stays quiet on an ordinary weekly basket and flags a
mislabelled gift card, a 40x price outlier, a quantity slip, and an item name
carrying a prompt injection — which it reports as an injection rather than
obeying. Five of those are pinned in `tests/test_live.py`.

**Not yet exercised:** the subsequent-payment charge. `charge()` is written but
cannot run until a mandate is actually registered, which needs a human to
approve the UPI Autopay request in a PSP app. Everything up to that point runs
against live Razorpay.

Next: the buyer agent over the mock merchant, and reconciliation of a real
charge once a mandate is registered.

## Commerce: an adapter, and a mock behind it

The commerce integration sits behind one interface, and the engine's half of it
is a single method:

```python
class CommerceAdapter(Protocol):
    def fetch_cart(self, cart_id: str) -> Cart | None: ...
```

That narrowness is deliberate. A merchant integration has two audiences and
they must not be confused:

- **Agent-facing** — search, browse, build a cart. Rich, and completely outside
  the trust boundary. This is where a compromised agent slips a smartwatch into
  a grocery run.
- **Engine-facing** — `fetch_cart`, and nothing else. One independent read of
  what the merchant actually holds.

`MockMerchant` implements both. Its catalog is tuned so the demo's numbers fall
out of real items rather than being asserted: the twelve staples total exactly
₹1,850, and adding the earbuds and phone case makes ₹2,400 across 14 items.
A test pins both, so the escalation screen can never drift from the basket.

The mock is not a downgrade. Because we control the catalog, the lying-agent
scenario is deterministic — the agent builds a cart with a ₹15,000 item in it,
reports the ₹1,850 grocery subtotal (a *true* number, for the groceries alone),
and the engine's independent fetch catches it every single run.

**On a real provider.** The question to ask of any of them is not whether the
agent can shop — it is whether the *engine* can independently read back what
the agent built. Without that, Layer 0 has nothing to verify against and
provenance degrades to trusting the agent.

Swiggy's Instamart MCP server passes, with two caveats worth writing down.
It exposes `get_cart` — "the authenticated session's current cart with all
items and bill breakdown" — so the independent read exists. But `get_cart`
**takes no arguments**: the read is scoped to the MCP session, not to a cart id.

1. **The engine needs its own authenticated session.** Session-scoped reads
   only mean something if the engine holds its own OAuth credentials. An engine
   that asks the *agent* to call `get_cart` and relay the answer has not
   verified anything — it has trusted the agent with an extra step, and Layer 0
   becomes decoration while every test still passes.
2. **The snapshot is not pinned.** One mutable current cart per session, and
   `update_cart` replaces it wholesale, so the cart can change between the
   agent's proposal and the engine's fetch. `MockMerchant` cannot exhibit this
   — its carts are immutable and id-addressed — which makes reserve-then-commit
   a real requirement on the live integration rather than the optional item it
   is here.

A Swiggy adapter therefore implements `fetch_cart(cart_id)` by ignoring the id
and calling `get_cart()`, the id degenerating to a session handle. That shape
difference is precisely what the adapter exists to absorb.

## The rail: UPI Autopay

Chosen over card mandates because `as_presented` — variable amount per debit —
is native to UPI Autopay. That is the whole answer to *"why not just Razorpay
Subscriptions?"*: Subscriptions governs a fixed schedule, and a static cap is
provably insufficient for an agent whose basket total moves every run. Card
mandates would cost us that story.

Verified against Razorpay's docs before building:

| Fact | Value |
|---|---|
| `as_presented` frequency | supported (also monthly, weekly) |
| `max_amount` | paise; defaults to `9999900` (₹99,999), min ₹1 |
| `expire_at` | Unix timestamp, **defaults to 40 years** |
| Per-transaction ceiling | ₹15,000 — the same line RBI draws for AFA |

**A UPI Autopay token does not expire in ~3 days.** That is card test-mode
behaviour, and building a demo around it on this rail would mean waiting for an
expiry the sandbox never delivers.

So a mandate dies because *the engine says so*, never because Razorpay timed it
out — `status` (revoked / paused) and `expires_at` are engine state, checked
before any policy evaluation. This is the right shape regardless of rail: the
boundary is ours to enforce, and revocation must not depend on a sandbox that
returns success for cancellations it never performed.

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
