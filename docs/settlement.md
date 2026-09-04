# Settlement

Three legs, told apart by who is standing there — and a full account of what this Razorpay test account will and will not permit, with the live payment ids to check it against.

[← back to the README](../README.md)

---

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


### The third leg: a one-time purchase

The standing rule covers the shopping. It does not cover the thing you needed
once, and a ₹15,000 smartwatch under a groceries-only ₹2,000 rule is refused on
three separate counts. **That refusal is correct.** It is not the end of it.

`POST /api/mandate/one-time` takes a cart id and nothing else. What comes back
is not an exception and not a raised cap — it is a **second mandate**, compiled
from the basket the *engine* fetched:

| Bound | Where it comes from |
|---|---|
| cap | the cart's own total, to the paise |
| merchant | the cart's merchant |
| categories | the cart's own lines |
| delivery address | the cart's address, **and only if a standing rule already ships there** |
| cadence | once, then it is gone |
| expiry | fifteen minutes |
| basket | `Policy.cart_id` — this one, not one like it |

Then `decide()` rules on it, exactly as it ruled a moment earlier. The engine is
not told which kind of mandate it is judging, and no new verdict path exists.

Four things it deliberately cannot do:

- **It cannot introduce a delivery address.** A grant widens *what* and *how
  much*; the card a user approves shows a price and a shop, which is not where
  anyone notices a stranger's doorstep. Minting is refused outright instead.
- **It cannot be spent on a different basket.** `Policy.cart_id` is the new
  bound and the only one added to the engine for this. Without it, an approval
  for a ₹15,000 smartwatch is also an approval for any other ₹15,000 basket at
  the same shop for the next fifteen minutes — the substitution nobody looked
  at. A different cart is `grant.other_cart`, a DENY.
- **It cannot be minted by the agent.** There is no tool that reaches the route,
  for the same reason none writes the shopping list.
- **It cannot survive the basket moving.** Cart ids are content-addressed, so a
  basket that changed after approval answers to a different id, and the grant
  stops handing out its checkout rather than settling a total nobody re-read.

The app's part is one button on a refusal, and it sends a cart id. Everything it
gets back was written server-side. `GET /pay` is a page, not a route that moves
money: the order it renders was created by `_settle` under an ALLOW, and a test
spends the whole flow proving it cannot mint one.

Captured live: escalation `category.not_allowed+cap.exceeded+frequency.exceeded`,
then a grant, then `ALLOW` and a real test-mode order — `order_TUNxpcqCUkcZBL`.


### Verified against the live test API

| Fact | Result |
|---|---|
| Orders API with a mandate token object | works — real orders created |
| `frequency: "as_presented"` | accepted, on both `upi` and `card` |
| `token.type: "single_block_multiple_debit"` | accepted |
| `max_amount` ceiling for this MCC | exactly ₹1,00,000 (`10000000` paise) |
| Token object actually validated | yes — a bad `frequency` and an over-ceiling `max_amount` are both rejected server-side |
| Checkout modal | loads, renders the correct mandate terms, accepts card entry |


### The first payment through the product path

Not a probe — a proposal the engine authorised, settled on live Razorpay rails,
reconciled into the hash-chained ledger:

```text
#0  ALLOW    ok.in_policy   185000 paise   key=f90e60c8f1b8
#1  SETTLED  pay_TTMncCDOzWLlpK            signature_verified=True
chain_intact: True
```

```text
pay_TTMncCDOzWLlpK   captured   ₹1,850   card   order_TTMn6oHEMScYXI
```

The gate held in both directions. `POST /api/proposal` runs the engine first,
and the Razorpay order is created **only** on `ALLOW`:

| The agent proposes | Verdict | Reached the rail? |
|---|---|---|
| the usual basket, reported truthfully | `ALLOW` | yes — order created, ₹1,850 captured |
| ₹1,850 claimed over a cart hiding a ₹15,000 smartwatch | `DENY` | **no** |
| earbuds and a phone case, ₹2,400 | `ESCALATE` | **no** |

The amount sent to Razorpay is the total the engine *fetched*, never the total
the agent claimed. A test asserts that too.


### A completed test payment

Razorpay gates account activation on one real test transaction. Done, driven
through the actual Standard Checkout modal:

```text
payment_id  pay_TTMX1mGSIy1mO4   status captured   ₹1   method card
order_id    order_TTMWRVfH9sgCuZ
```

The callback signature was then put through this codebase's own verifier:

| Input | Result |
|---|---|
| the genuine signature Razorpay returned | `200 {"verified": true}` |
| the same signature, one byte changed | `400 signature verification failed` |

That is the verification path proven against a real Razorpay signature rather
than a synthetic one.

**What actually blocked this for several attempts:** the first key pair was a
rotated one, and checkout authenticates with `key_id` alone while the server API
uses `key_id` **and secret** — so the server API kept returning real orders from
a key checkout had already stopped accepting. A fresh pair fixed the checkout
leg immediately.

`activated: false` on the account was **not** the cause — this payment
succeeded with the account still in that state.

Two claims that used to sit here have since been **disproven by retesting on a
freshly issued key pair**, and are corrected below rather than quietly deleted:

- *"A fresh key pair makes server-side payment creation work."* It does not.
  `POST /v1/payments/create/ajax` answers `401 Authentication failed` to server
  credentials no matter how new they are, because that route does not accept
  them; and it answers a plain nginx `403` to a server presenting `key_id`,
  because it is browser-only. The ₹1,850 capture was made from a browser.
- *"Regenerating keys is one of the three things needed to unblock."* Rotation
  changes nothing about capability. Capability is a property of the account, not
  of the key — verified by comparing `/v1/preferences` across an old and a new
  pair on the same account and getting an identical answer.


### Saving a card, in test mode

Charge orders now carry a `customer_id`, which is what Razorpay tokenises
against. Without it the account had two captured payments and **zero saved
cards**, so every checkout asked for a full card — our omission, not a property
of hosted checkout.

With it: the card is entered once, and Standard Checkout runs the **CVV-less
flow by default** on tokenised Visa, Mastercard and Amex — no enablement
request. So the second approval is a tap and an autofilled OTP.

Test mode has everything needed to demonstrate this, and `/pay` shows it when
the key is a `rzp_test_` one rather than making anybody go and look it up:

| | |
|---|---|
| India test card | `4100 2800 0000 1007` (Visa) |
| Expiry / CVV | any future date, any three digits |
| OTP | any 4–10 digits succeeds; under 4 fails |
| Token life | **3 days** in test mode |

That last row is the one to plan a recorded demo around: a card saved on Monday
is not saved on Friday.


### What the account does not yet permit

`GET /v1/preferences?key_id=…` is the authority here — note it authenticates
with `key_id` alone; sending server credentials gets a `401`. On a freshly
issued key pair it reports:

```text
activated: False
card: True    upi: False    nach: True    cod: False
emandate, recurring, debit_card_recurring:  absent from `methods` entirely
features matching /recur|s2s/:              none
```

**Every route that would move a customer's money without a human present is
closed, and each for its own reason:**

| Route | Gate |
|---|---|
| mandate debit (`payment.createRecurring`) | needs a token from a registered UPI Autopay mandate; `upi: false` and no `recurring`/`emandate` |
| documented server-to-server card charge | needs S2S enablement; no such feature on the account |
| checkout's own `payments/create/ajax` | browser-only — `403` from nginx to a server, `401` to server credentials |

Order-creation success is **not** evidence that a method is available, and
neither is a fresh key. The `/v1/preferences` response is.

Independently confirmed against the **Razorpay MCP server**, which exposes 35+
tools over the same account: its payments category is `capture_payment`,
`fetch_payment`, `fetch_payment_card_details`, `fetch_all_payments`,
`update_payment` — **no create** — and there is no tool for recurring,
subscriptions, mandates or tokens anywhere in it. An MCP wrapper cannot grant a
capability the account lacks.

**Saved cards do not work either, and for the same reason.** The integration is
correct — the customer exists, Checkout sees it, `customer_id` reaches the
options object, the order carries it, `remember_customer` is in the account's
own `options` list. A card was entered and saved. What came back:

```text
Customers API   /v1/customers/{id}/tokens   count: 1
                token_TVkC6GvnCXtmX3   status: failed   Visa ****1007

Checkout API    /v1/preferences?customer_id=…   tokens: 0
                — under every parameter combination tried
```

The token is created and then fails, so Checkout offers nothing and shows an
empty card form. Nothing in this codebase changes that.

Unblocking needs exactly one thing, and it is not code: **account activation,
and with it recurring / UPI Autopay**. That one change unblocks three separate
walls — the mandate debit, server-side charging, and card tokenisation.
`charge()` is already written and tested against that day.

**The fallback, if that is refused.** Razorpay orders support manual capture
(`payment_capture: 0`): the user authorises once in checkout, the payment sits
`authorized`, and the engine **captures it server-side after its verdict, with
nobody present**. That is real money moving unattended, on an account that
cannot do recurring, and it mirrors the two-leg shape this README already argues
for. It is not a mandate — a card authorisation is a fixed amount with a short
expiry, so it cannot be reused for a different basket — and it is untested here,
so it is written down as a plan rather than a result.

**Payment Links are deliberately not the answer.** They work on this account
(verified — `plink_TUWAJxRD3uQfpl` created server-side, nobody present), but a
link the agent sends you to approve is precisely the confirm dialog this product
exists to remove. It would make the demo visible and the thesis weaker.


### Running the payment path

```bash
set -a; . ./.env; set +a
uv run uvicorn bounded_mandate.web:app --reload --port 8117
```

The port is not incidental: the app looks for the engine on :8117, so the
default 8000 gives you a healthy server nothing can reach.

Then open `http://127.0.0.1:8000`, edit the rule, press **Read it back** to see
it compiled, and **Confirm and register** to open Razorpay's modal in test mode.


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
