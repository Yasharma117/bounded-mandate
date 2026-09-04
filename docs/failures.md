# What broke at 3am

Eight real failures, each naming the commit that fixed it. A list of features tells you what somebody intended; a list of failures tells you what they actually ran.

[← back to the README](../README.md)

---

## What broke at 3am

Every one of these was real, and every one names the commit that fixed it. They are collected here because a list of features tells you what
somebody intended and a list of failures tells you what they actually ran.


### A greeting bought the groceries

**"Hey hello", in voice mode, put ₹1,850 of groceries on Razorpay's rails.** The
transcription was correct and the engine ruled it correctly — squarely inside
the mandate — and that is the uncomfortable part. **Containment held; restraint
failed.** The engine bounds *what* may be bought, never *whether anyone asked*,
and it cannot be made to tell the difference because that is not a question
about authority. The cause was the agent's own system prompt reading as an
unconditional procedure — *"Work in this order: 1… 2… 3…"* — with nothing gating
it on having been asked for anything. It decides first whether it is being asked
to buy now, and answers small talk with no tools at all, not even reading the
list. Three live tests pin it, including one that a greeting reaches the engine
zero times. (`d48480b`)


### The escalation you could dismiss before it happened

**An interrupt could be silenced in advance.** Idempotency keys are
`sha256(mandate | window | cart)[:32]` — deterministic — and cart ids are
predictable on both backends, sequential on the mock and a content hash on
Swiggy. So the key of a decision nobody has made yet is computable, and
`POST /api/home/seen` accepted it. Demonstrated end to end: predict the next
cart id, dismiss its key, then make the engine escalate on exactly that cart —
and the escalation never reaches the screen. No money was ever at risk. What was
at risk is the only channel by which the user finds out, and **silencing the
interrupt defeats an escalation as thoroughly as widening the cap would, while
looking like nothing happened.** A dismissal is now refused unless the decision
it names is already in the ledger. (`8447a7c`)


### Six ALLOWs for one basket, and a chain that broke itself

**Two things happening at once was enough**, and this app already does two things
at once: every write route is a sync `def` running in anyio's threadpool, and the
scheduler adds a thread per tick. `Ledger.append` was read-head → compute →
write with no lock, so 8 threads × 25 appends produced `CHAIN BROKEN:
out-of-order seq 0` — the tamper-evidence screen accusing itself, in front of
whoever was being shown the tamper evidence. The one that moves money was worse:
`decide` read the charged keys and `_record` wrote much later, so six concurrent
copies of one cart returned **six ALLOWs**. Locking `append` alone fixed
nothing — measured, not assumed — because the read that decides sat outside it.
The whole span from `_history` to `_record` is one critical section now, and the
ceiling is named where it is paid: the lock is held across the model call, so
decisions serialise behind a network round trip. Free at one user, wrong at a
thousand. (`842e415`)


### A signature proves who spoke, not that we asked

**`/api/settlement/verify` checked Razorpay's signature and wrote SETTLED
regardless of whether the order was one of ours.** The signature is real — it
proves Razorpay sent the triple — but any valid triple from any other flow on
the same account verifies exactly as well, including the ₹1 registration. A
replayed callback wrote an entry matching no grant, and the home screen then
said *"Paid — your order. It is on its way."* about an order nobody placed. The
one card in the app that claims money moved was the one card that did not check.
Authenticity and authorisation are separate questions now, which is the split
the engine makes everywhere else. The test that covered this had passed without
ever minting a grant, which is precisely the hole. (`7751f3f`)


### An iPhone is not a grocery because Apple is a fruit

**`Apple iPhone 17 Pro` came back `groceries`, `category_allowed=True`, on a
₹1,29,900 line.** The category table asked only whether `apple` appeared
anywhere in the name. The cap stopped that particular basket, which is the wrong
guard doing the work — a ₹900 Apple Watch band clears both checks. The same hole
sat under `Rice cooker`, `Egg boiler`, `Milk frother` and `Cheese grater`:
appliances named after the food they handle. The table is ordered and first match
wins, so the fix was ordering rather than cleverness. A stress test had already
written this up as a known limitation — *"no cheap signal separates them"* —
which was wrong: `frother` is a cheap signal, it just had to be looked at first.
(`76d6b2b`)


### A rail failure freed a paid basket forever

**A retry of the same cart in the same window is the same idempotency key by
construction.** A rail failure vetoed that key and nothing ever lifted the veto,
so: allow, the rail fails, the retry succeeds and is paid — and the paid basket
could still be authorised a third time, while the charge that actually went
through never spent the window. Measured, with a cap of one order: the third
proposal came back ALLOW. The last outcome per key wins now, so the retry's own
ALLOW counts itself. (`f699da6`)


### Approving the basket ended the argument, and the agent never heard

**Something out of scope escalated, was approved, was paid — and the next turn
rebuilt the same basket and read the same refusal back, forever.** The approval
and the payment lived entirely in the card's own state, so the thread never
recorded either, and the thread is what feeds the agent its history. Its next
turn was handed a conversation ending on its own refusal, and rebuilding the
basket is the only move that history supports. The button was the other half of
why this was reachable at all: a tinted row under a divider, the same shape the
card uses for rows you only read, so it went untapped — and **a refusal with an
invisible way out is a refusal with no way out.** Found in a dress rehearsal,
along with `int(True) == 1` quietly turning `every_days: true` into a daily
order. (`12ed69a`)


### The room was talking to the agent

**Voice mode listened continuously**, ending a turn on 1.4 s of quiet, which is
the better interaction in a quiet room with one person in it and the wrong one
during a recording. Narrating what the app does to an audience arrived as
utterances for something that spends money, and no amount of meter tuning tells
narration from instruction. The circle is held now: the microphone is open while
your finger is down and shut the moment it lifts, so between turns nothing is
recorded, uploaded or heard. The transcript filter stayed, because holding a
button does not empty the room.

---

All eight left a runnable check behind. As for how they were found: three came
from tests written to attack our own surfaces, three from using the app on
camera, one from an outside review, and one from noticing that a test had passed
for the wrong reason.
