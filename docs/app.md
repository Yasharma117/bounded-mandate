# The app

Warden — a native SwiftUI client that holds no policy and no keys. It renders verdicts it did not compute and cannot appeal.

[← back to the README](../README.md)

---

## The app

The client is a native SwiftUI app in [`ios/`](../ios), targeting iOS 26. It is a
thread: you say what you want, the agent shops, and the engine's verdict lands
in the conversation as a card.

The app holds **no policy, no Razorpay key, and no ElevenLabs key**. It renders
verdicts it did not compute and cannot appeal. Decompiling the bundle yields
nothing, because there is nothing in it — every credential lives in the engine
process, and the phone talks only to the engine's host, including for voice.

```bash
./scripts/engine.sh start                                     # the engine
cd ios && xcodegen generate && open BoundedMandate.xcodeproj  # the app
```

`engine.sh` also takes `stop`, `restart`, `status` and `log`. It starts the
server in a session of its own, so closing the terminal — or anything that
signals the process group that launched it — leaves it running. That is not
hypothetical tidiness: started as an ordinary background child, the engine died
every time its parent did, and the app's honest "Can't reach the engine" then
looked like a new bug in whatever part of the product was on screen.

`status` reports two different things, because they fail separately:

```text
running (pid 55079) on :8117 — answering
shop: swiggy  reachable=True
```

Foreground, if you would rather watch it:

```bash
set -a; . ./.env; set +a
uv run uvicorn bounded_mandate.web:app --host 0.0.0.0 --port 8117
```

`Engine.baseURL` defaults to `http://127.0.0.1:8117`, which the simulator
reaches directly; override it with a `BMEngineHost` user default for a device.


### Why SwiftUI and not React Native

The first client was Expo. It was replaced once it became clear the design
required Liquid Glass, which is a first-party SwiftUI API — `.glassEffect()`,
`GlassEffectContainer`, `glassEffectID`. Through the React Native wrapper the
container would not composite across a `ScrollView`, glass had to be given a
hand-built backdrop to refract, and cards needed manual borders and shadows to
avoid dissolving into the page. All of that is one modifier here.

The swap cost seven files and no server code. The engine, ledger, compiler, NIM
binding, Razorpay integration, buyer agent and both voice routes were untouched,
because the client only ever spoke HTTP — the fourth time that has now been
demonstrated rather than asserted.


### One card for four verdicts

A receipt and a refusal are the same view wearing different colours. That is the
honest shape: the engine ran the same checks either way, and the reader should
be able to parse both the same way. When the agent misreports its own cart the
card shows the real total in the headline and the claimed total beneath it, in
the refusal colour.


### Colour is Razorpay's, not invented

`Theme/Tokens.swift` carries Blade's own values, resolved through Blade's
semantic mapping in `packages/blade/src/tokens/theme/bladeTheme.ts` —
`surface.background.gray.*`, `surface.text.gray.*`, the `interactive` primary,
and the four `feedback` hues. Each token names the Blade scale step it came
from, so it stays diffable against upstream. Light and dark both.

`ALLOW` is Razorpay blue rather than Blade's `positive` green: the brand colour
is the colour of *authorised*, which gives it a job instead of spending it on
furniture. The other three keep Blade's feedback semantics — `orchid` for
CLARIFY, `cider` for ESCALATE, `crimson` for DENY.


### Voice

Speech is an **utterance**, not an authority. A transcript reaches the agent
with exactly the standing that typing has, and there is no verdict reachable by
voice that is not reachable by text — so widening the input channel does not
widen what the engine will approve. `POST /api/voice/transcribe` takes raw audio
bytes and returns text; it touches neither the ledger nor the gateway, and a
test asserts that.

Audio round-trips through the engine's host rather than going to a provider
directly, for the same reason the Razorpay secret does: a key shipped inside an
app is a published key.

**Hearing is ElevenLabs Scribe; speaking is either service.** Two synthesisers
are wired behind one seam and swapped with `BM_TTS_PROVIDER`, so which voice
ships is a decision made by ear on the recorded demo rather than argued about in
advance. `POST /api/voice/speak` takes an optional `provider`, which is how both
can be compared on the same sentence without restarting anything, and it answers
with the content type the provider actually produced rather than one the app
assumed.

| | Hearing | Speaking | Format |
|---|---|---|---|
| ElevenLabs | Scribe v2 | `eleven_flash_v2_5`, Sarah | mp3, ~1.3 s |
| Rumik Silk | — | `mulberry`, Indian-English voices | 24 kHz wav, ~2.6 s |

Rumik does not transcribe, so swapping the speaker must not silently swap the
listener to something that cannot listen. A test pins that.

Text-to-speech failures are swallowed on purpose. Losing audio should never cost
the user a decision they can already read on screen.


### Two voices, switchable mid-sentence

Both ElevenLabs and Rumik have been wired since the voice work landed, and the
keys for both are configured. Neither the picker nor `Voice.providers()` was
ever called from the app, so Rumik was reachable and unselectable — built, and
in practice dead.

The control now sits in voice mode itself, because the difference is one to
judge by ear rather than from a table. Measured on the same sentence:

| | latency | payload |
|---|---|---|
| ElevenLabs | 0.60s | 75 KB `audio/mpeg` |
| Rumik (Silk, `mulberry`/`siya`) | 2.43s | 264 KB `audio/wav` |

Four times slower is four times the silence before it answers, which in a
conversation is the thing you notice first — and it is the sort of trade-off
that only reads properly out loud.

Which voices exist is answered by the engine, never guessed by the app: the keys
live server-side, so offering one that is not configured would be offering a
503.


### Voice mode

Typing and talking are separate doors. The field is for typing and stays
**silent** — answering a typed line out loud is the app talking over you. The
button beside it opens a conversation, and it sits outside the text field
because it does not send that message; it opens a different way of talking
altogether, and a control inside the field would promise otherwise.

**Hold the circle to talk.** The microphone is open while it is held and shut
the moment it is let go; between turns nothing is recorded, nothing is uploaded
and nothing reaches the agent. Release is the whole end-of-turn rule, so there
is no timer to tune and no threshold to be wrong about.

It ran itself before this — listen until 1.4 s below −38 dB, transcribe, answer,
listen again, nothing pressed twice — and hands-free is the better interaction
in a quiet room with one person in it. It is the wrong one everywhere else. A
demo is the clearest case: the person holding the phone is narrating to an
audience, and every sentence of that narration arrived as an utterance for an
agent that spends money. Explaining what the app is about is not a shopping
instruction, and no amount of meter tuning can tell the difference. A press can.

The transcript filter stays, because holding the button does not stop the room:
a television behind you is still in the recording, and anything bracketed or
under two words still goes nowhere near the agent.

Holding while it is speaking interrupts it, which is what a person does. Leaving
voice mode is its own control beside the orb — the orb is the microphone now,
and one control that both takes your sentence and closes the screen would pick
the wrong one at the worst moment.

The screen starts almost empty, because before you have said anything there is
nothing true to show. What it does instead is *react*: the `MeshGradient` is
driven by live audio level — your voice while it listens, the agent's while it
speaks — so a screen with no words on it still reads as listening, and never
looks frozen during the seconds where nothing has been decided. Cards arrive
only as the conversation earns them.

**Cards arrive as the conversation earns them.** Asking what something costs
puts the comparison on screen and the agent says one sentence — every price is
already in front of the person it is talking to, and reciting a table aloud is
the worst thing a voice can do. The agent also cannot say which shop is
permitted, because it is not allowed to know: offers are annotated with the
policy's verdict **on the way out to the app**, never in the tool result the
model saw. A test asserts the model was handed them bare.

**The room is not a user.** Scribe tags non-speech as `[music]`,
`[outro jingle]`, `[silence]`, and those arrive looking exactly like an
utterance. Forwarding them would let a television in the background talk to an
agent that spends money. Anything bracketed, and anything under two words, goes
back to listening without reaching the agent — found by watching an idle
simulator send `[outro jingle]` to the engine as an instruction.


### The shopping list

"My usual groceries" used to be a tuple in Python that the user could not see or
change. It is now a document they own — [`basket.py`](../bounded_mandate/basket.py)
— editable over HTTP, priced against the merchant their mandate allows, and
shown with the cap it has to clear.

**The list is not the policy.** The policy bounds *how much*; the list defines
*what*. Keeping them apart matters: raising a cap and adding an item are
different decisions, and collapsing them into one object would make every basket
edit read as a spending change. A test asserts that editing the list writes no
ledger entry and moves no cap.

**The agent reads it and has no tool that writes it.** That absence is a
security property, not an oversight. An agent that could redefine "usual" could
then order the new definition entirely within policy — an escalation that never
trips a bound, arriving through a door nobody was watching. Two tests pin it,
one of which calls the dispatcher with an invented write tool and checks the
list is unchanged.

The card carries a cap meter rather than two numbers side by side, because a
number beside a number is arithmetic the reader has to do themselves. A list
that cannot clear the policy says so while it is still editable, instead of
after an agent run reports an escalation.


### Cards

Every card was built against a dev gallery — every state on one scroll —
because iterating design against the real thread costs a six-second model
round-trip and a ledger reset per state. It was scaffolding and it has been
removed: the shipping build has one entry point, no launch flags that change
what the app is, and no screen a user cannot reach.

| Card | States | What it answers |
|---|---|---|
| Decision | allow, clarify, escalate, deny, captured | what the engine ruled, why, and whether money moved |
| Shopping list | inside cap, over cap, unstocked line, read-only | what I buy, what it costs, will it clear my rule |
| Offers | several shops, sole shop | who sells it, for how much, which of them my rule reaches |
| Mandate | standing, one-time grant | exactly what I have authorised |

Things found by looking at renders rather than by reasoning about them:

- an allowed shop selling a disallowed category read as **"not on your list"**,
  naming the wrong reason. The server now answers merchant and category
  separately, and the card says which one it was. Naming the wrong reason is
  worse than naming none.
- a twelve-item list filled the whole thread. Collapsed to four, with the total
  and the cap meter kept above the fold, since those are what the reader came
  for.
- a refusal saying "2 items outside your scope" showed no cart. The decision now
  carries the cart the *engine fetched*, each line marked by the policy, and the
  card opens it by itself when something in it is the reason for the verdict.
- `1 SHOPS`, `once every 1 days`, and a "cheapest" badge on a sole offer.


### Testing the cards

`ios/BoundedMandateTests` decodes payloads **captured from a running engine**,
not hand-written JSON, so the failure it catches is the server changing shape. A
card that silently renders a default because a key was renamed is worse than one
that fails to render, and money figures are exactly where that must not happen.

It earned itself twice during the build: once when a stale `uvicorn` was still
serving `in_policy` after that field had been split in two, and once when a
fixture predated the `merchant` key. Seventeen tests, including that the flagged
lines and the reason prose agree with each other, and that the cart lines sum to
the figure printed above them.

```bash
cd ios && xcodebuild test -scheme BoundedMandate \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```


### Three shops, and why that matters

`Marketplace` routes on a cart-id prefix, so the engine still sees one adapter
and still checks `cart.merchant` against the policy — unchanged. Blinkit
deliberately undercuts Instamart on the staples, so **"cheapest" and "within
your rule" point at different baskets**. That is the only honest way to show a
merchant allowlist is a real constraint rather than decoration, and the offers
card states the premium out loud: *"Staying on your list costs ₹16 more."*

Product links resolve to a real storefront rather than 404ing, which also gives
the injected catalog item a page where the planted instruction sits in plain
sight of anyone who looks.


### The agent, over HTTP

`POST /api/agent` hands an instruction to the buyer agent and reports what it
did — every tool call, and what the engine made of the proposal. The
`adversarial` flag swaps in an agent working against the account holder. It
changes what the agent *tries* and nothing about what the engine permits, which
is the only reason it is safe to ship a button for it.

Four openers, four outcomes, all verified against the live model and the live
Razorpay test API:

| Said | Verdict | Why |
|---|---|---|
| *milk, eggs and brown bread* | `ALLOW` | ₹215, in policy — a real Razorpay order is created server-side with nobody present |
| *my usual groceries* | `ESCALATE` | the agent obeys a prompt planted in a catalog item and adds ₹15,000 of saffron; `cap.exceeded` |
| *…plus earbuds and a case* | `ESCALATE` | `category.not_allowed+cap.exceeded` |
| *compromised agent* | `DENY` | `provenance.total_mismatch+category.not_allowed+cap.exceeded+agent.probing` — it claims ₹1,000 for a ₹16,850 cart, then retries |

The third row is the one worth sitting with: the agent is not compromised, and
it still nearly bought ₹15,000 of saffron because the *merchant's catalog* told
it to. Nothing about the agent's alignment was load-bearing.


## The home screen, and its states

The app's home was a chat thread that opened with two seeded messages. It told
you nothing until you spoke to it — which is the wrong metaphor for a product
whose whole claim is that **nobody is present**. A chat-first home says "drive
me by talking"; the thesis says "I ran while you were asleep."

Worse, an unattended decision had nowhere to land. The scheduler proposes at
nine, the engine rules, the ledger records — and if you were not in the thread
at that moment, nothing ever told you. Home now answers one question before it
is asked: *where do I stand?*

**The server decides which state that is.** `GET /api/home` returns the state,
the words for it, and the routes it offers — the same rule that already puts
`off_scope`, `merchant_allowed` and `authorised` server-side, because whether
something needs the user is the policy's judgement and a client should not be
reimplementing it. The prose lives in
[wording.py](../bounded_mandate/wording.py) beside the reason titles, for the
reason that module already existed.

Precedence: something waiting on you, then money already committed, then money
that actually **moved**, then the newest thing that happened, then the next
thing due.

That third rung is its own state because an order is not a payment — a
distinction this codebase insists on everywhere and had no screen for. A settled
payment used to fall through to `ruled`, which says "placed while you were
away": wrong twice over, since the reader was standing there paying and nothing
told them the money had actually gone.

| State | What it leads with | Offers |
|---|---|---|
| `at_rest` | "Your rule is running." | view rule · pause |
| `preflight` | "My usual groceries goes out shortly. ₹1,850 of your ₹2,000 cap. **Nothing for you to do.**" | pause · view basket |
| `ruled` | "Ordered — ₹1,850, inside your rule. Placed **while you were away**." | view basket · verify the chain |
| `paid` | "Paid — ₹215. It is on its way." Carries the Razorpay reference. | view basket · verify the chain |
| `needs_you` — escalation | "Your call on ₹2,400." Names *which* two items. | approve just this basket · remove the flagged items · not now |
| `needs_you` — refusal | "Refused, and nothing was charged." | see what it tried. **Nothing else.** |
| `needs_you` — clarify | "One line needs an answer." | add to my list · approve once · leave it out |
| `needs_you` — halt | "Halted — that is not an address you authorised." | re-authorise · cancel the basket |
| `grant_live` | "Approved — ₹15,000, this basket only." | pay · let it lapse |

Two properties the set is careful about.

**Options are proposed and never taken.** That is not a UI convention here, it
is the engine's contract with the agent rendered as buttons — and it is why the
refusal arrives with no approval among them. An agent caught misreporting its
own basket is not a thing to wave through with one tap, and the absence is
visible in the same table as everything else.

**The halt is recorded, not only returned.** Trying to approve a basket bound
for an address the rule no longer covers is refused with a `403` — and written
to the ledger as a `HALTED` event, so it becomes a state the reader can look at
afterwards rather than an error the app swallowed. Dismissing any of these
appends a `SEEN` entry rather than mutating anything: this ledger is
append-only, and "the user looked at it" is the same class of thing as the
decision it dismisses.

Layout, hierarchy and the timing of what surfaces when are taken from the Nola
reference; **no colour is** — the palette is unchanged. What that reference is
really good at is one hero figure carrying the hierarchy, prose before
structure, options stacked rather than rowed, and the least urgent thing
deliberately passing under the command bar.

Also closed on the way: `MandateCard` was only ever reachable from a one-time
grant, so **the standing rule — the central object of the product — had no
screen and no route.** It is the page header now.


### The voice agent holds a conversation

The loop was already listen → transcribe → answer → listen again. What made it
a sequence of unrelated commands rather than a conversation was that every turn
arrived with no idea what the last one was.

The agent is now given what was **said** — the user's words and its own replies,
capped at ten turns so a long session cannot push the system prompt out of the
model's attention. Tool calls and results are deliberately left out: a cart id
from three turns ago is not context, it is a reference the agent could charge
against long after the basket stopped existing, and Layer 0 would then be
refusing a cart nobody meant to propose.

```text
you : what's on my list?
it  : Here's your list: Aashirvaad atta 5kg, Basmati rice 1kg, ...
you : what would that cost?
it  : The cheapest total is ₹1,736.00 at Blinkit. Would you like me to order?
you : alright, order it
it  : Your order has been authorised. The total is ₹1,850.00.      [ALLOW]
you : and was that within my limit?
it  : Yes, the order was authorised, so it was within your limit.
```

Note the fourth turn: it mentioned a cheaper shop and then ordered at Instamart
anyway, because it cannot change shops on its own. It also speaks its own words
now — a canned `narrate` string used to override the agent whenever a decision
existed, so every order sounded identical to every other one.


## Where things get delivered

The third thing the user owns, and the one with the sharpest edge on it. A
mandate that bounds the cap, the shop and the scope is worth nothing if an agent
can move the doorstep: ₹1,900 of perfectly ordinary groceries, entirely in
policy, sent to a stranger.

So the address book is read from the merchant, the **user** picks one, and that
choice is pushed down to the commerce session and up into the policy in the same
act. `GET /api/addresses` and `PUT /api/address` are the only routes that touch
it, and no agent tool reaches either — `_create_cart(item_names, merchant)` has
no third argument, which is why an injected prompt has nowhere to put an
address. A test asserts that signature.

Selecting *is* authorising, which is honest rather than lax: every row is
already an address on the user's own account, so there is no third party to
introduce. What the mandate stops is somebody else adding one — the agent, or a
one-time grant, which is refused outright rather than shown on a card that
displays a price and a shop.

The new address **replaces** the authorised set rather than joining it. A
mandate should authorise where you actually deliver; addresses that accumulate
are authority nobody remembers granting.


### Authority never travels as prose

Found by running it. Swiggy returns the same address formatted two different
ways depending which endpoint answered:

```text
get_addresses  "<name>: <flat>, <area>, Sector 14 Road, Sector 14, Gurugram, …"
get_cart       "Sector 14 Road, Sector 14, Gurugram, …"
```

Two strings, one place. A policy pinned to either is refused against the other,
and it fails as `delivery.unknown_address` — **indistinguishable from an agent
actually shipping somewhere it should not**. The `id` is byte-identical on both.

So `Policy.delivery_addresses` holds ids, `Cart.delivery_address` carries the id,
and the label and the street only ever reach the card. Matching authority
against presentation is a bug waiting for a merchant to reformat a string.


### A free thing is still a thing

A live cart came back carrying a festive rakhi nobody added: ₹89 in `items[]`,
absent from `Item Total`. The adapter refused the whole basket — correctly, but
for the wrong reason. Two different residuals were being checked against one ₹1
rounding tolerance, so a legitimate promotion looked exactly like a cart that
does not add up.

They are separate now. The goods reconcile against the bill's own item total via
a visible `Discounts and free items` line; what remains is rounding, and that
keeps its tight bound. Every line stays on the card at its listed price and the
arithmetic is still exact.

The freebie stays in the cart rather than being netted away, which is the part
that matters: it is still categorised, classifies as nothing, and reaches the
user as `category.unknown`. Something they did not ask for arrives as a question
rather than in the box.

```text
Floral Design Beaded Rudraksh Rakhi by Nanwan     ₹ 89.00   —
Amul Taaza Milky Milk 500 ml                      ₹ 28.00   groceries
Britannia Brown Bread 400 g                       ₹ 60.00   groceries
Discounts and free items                          ₹−89.00   fees
… fees …                                          ₹102.40   fees
Rounding                                          ₹ −0.40   fees
                                                  ₹190.00
ESCALATE  category.unknown+frequency.exceeded
```


## Product images

The cards showed no product imagery at all, which is a problem specific to what
this thing is for: the demo's punchline is a user *spotting* the item they did
not order, and a wall of text is a poor place to notice a smartwatch among the
groceries.

Swiggy already returns the photograph and the adapter was parsing straight past
it — `products[].variations[].imageUrl` on search, `items[].imageUrl` on the
cart. Public, no auth, and the host is Cloudinary-backed, so a transform segment
resizes on **their** CDN rather than ours:

```text
.../image/upload/NI_CATALOG/...png                  648,483 bytes
.../image/upload/w_160,h_160,c_fit/NI_CATALOG/...    10,702 bytes
```

Measured live. A twelve-line cart at full size would be 7 MB of photographs to
draw twelve thumbnails. The transform is applied server-side so the app never
learns the merchant's CDN scheme — the same reason `merchant_allowed` is decided
server-side rather than in the client.

Generic icon libraries were the alternative and lost on both counts: a 3D icon
of "milk" is worse than a photograph of the carton being bought, and the one
considered ([thiings.co](https://www.thiings.co/things), ~10,000 AI-generated
icons) is free only with attribution for non-commercial use, has no dal, ghee,
atta or Indian spices, and would have covered maybe two-thirds of even the mock
catalog.

**The mock has photographs too**, and they are real ones. Its products *are*
real products — the mock invents their prices, not their existence — so each of
the seventeen catalog lines carries the merchant's own photograph, captured once
from `search_products` and committed to `catalog_images.json`. Static, so the
mock stays offline: no session, no token, no network. One picture per product
rather than per seller, because the other shops sell the same thing at their own
price.

`ProductThumb` still draws nothing without a URL rather than a placeholder box,
so a fee line, an unmatched product or a rotted URL costs no layout.


### It is decoration, and the tests keep it that way

`CartItem.image_url` sits on the record Layer 1 reads, which is an invitation to
start reading it. Three properties are pinned:

- **A photograph does not change what a cart is.** `cart_id_for` hashes
  `name|price|category`, so a merchant swapping a product shot cannot invalidate
  an idempotency key or make the engine refuse a cart it already knew.
- **No verdict is ever reached because of a picture.** The same basket with and
  without one produces the identical reason code.
- **The agent is never shown one.** Merchant-controlled content handed to a
  model is an injection surface aimed at the component least able to refuse it —
  and this catalog already ships a prompt injection in a product *name*. The
  agent reads names and prices; pictures stop at the card.

A line is flagged by its text and its badge. The thumbnail sits beside that and
changes none of it, so a merchant serving a misleading photo — or none at all —
cannot make an off-scope item read as ordinary.
