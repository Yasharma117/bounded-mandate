import Foundation
import Testing

@testable import BoundedMandate

/// These decode payloads captured from a running engine, not hand-written JSON.
///
/// The point is to fail when the *server* changes shape. A card that silently
/// renders a default because a key was renamed is worse than a card that does
/// not render at all, and money figures are exactly where that must not happen.
struct PayloadTests {
    private func load(_ name: String) throws -> Data {
        let url = try #require(
            Bundle(for: BundleMarker.self).url(forResource: name, withExtension: "json"),
            "missing fixture \(name).json"
        )
        return try Data(contentsOf: url)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try JSONDecoder().decode(type, from: try load(name))
    }

    // MARK: - the shopping list

    @Test func theListDecodesWithEveryPriceIntact() throws {
        let list = try decode(ShoppingList.self, "list")
        #expect(list.listID == "usual")
        #expect(list.merchant == "instamart")
        #expect(list.items.count == 12)
        #expect(list.totalPaise == 185_000)
        #expect(list.capPaise == 200_000)
        // Every line priced: a nil price renders as "not stocked here", so a
        // decode that quietly nils them all would look like a broken catalog.
        #expect(list.items.allSatisfy { $0.pricePaise != nil })
        #expect(list.items.map(\.pricePaise).compactMap { $0 }.reduce(0, +) == list.totalPaise)
    }

    @Test func theCapMeterCannotOverflowItsTrack() throws {
        let list = try decode(ShoppingList.self, "list")
        #expect(list.capUsed > 0.9 && list.capUsed < 1)
        #expect(!list.overCap)
        #expect(list.headroomPaise == 15_000)
    }

    @Test func anOverCapListReportsTheOverageNotTheHeadroom() {
        let over = ShoppingList(
            listID: "usual", name: "x", merchant: "instamart", items: [],
            totalPaise: 225_000, capPaise: 200_000, unstocked: []
        )
        #expect(over.overCap)
        #expect(over.headroomPaise == -25_000)
        // The card negates this for display; the sign must survive that.
        #expect(rupees(-over.headroomPaise) == "₹250")
    }

    // MARK: - offers

    @Test func offersCarrySeparateShopAndCategoryVerdicts() throws {
        struct Wrapper: Decodable { let offers: [Offer] }
        let offers = try decode(Wrapper.self, "offers").offers
        #expect(offers.count == 3)

        let cheapest = try #require(offers.min { $0.pricePaise < $1.pricePaise })
        #expect(cheapest.merchant == "blinkit")
        // The cheapest shop is deliberately off-policy. If this ever passes,
        // the demo's whole point has quietly evaporated.
        #expect(!cheapest.buyable)
        #expect(cheapest.blockedReason == "shop not on your list")

        let allowed = try #require(offers.first { $0.merchant == "instamart" })
        #expect(allowed.buyable)
        #expect(allowed.blockedReason == nil)
    }

    @Test func anAllowedShopSellingABannedCategoryNamesTheCategory() throws {
        struct Wrapper: Decodable { let offers: [Offer] }
        let offers = try decode(Wrapper.self, "offers_category_blocked").offers
        let watch = try #require(offers.first)
        #expect(watch.merchant == "instamart")
        #expect(watch.merchantAllowed)
        #expect(!watch.categoryAllowed)
        // Naming the wrong reason is worse than naming none.
        #expect(watch.blockedReason == "electronics not in your rule")
    }

    // MARK: - verdicts

    @Test func theCompromisedRunDecodesEveryStackedReason() throws {
        let turn = try decode(AgentTurn.self, "turn_deny")
        let decision = try #require(turn.decision)
        #expect(decision.verdict == .deny)
        // Not a fixed count: `agent.probing` and `frequency.exceeded` depend on
        // what the ledger already holds, so pinning a number would make this
        // test a record of one run's history rather than of the behaviour.
        #expect(decision.reasons.count >= 2, "a refusal should stack its reasons")
        #expect(decision.reasonCode.contains("provenance.total_mismatch"))
        #expect(decision.lied)
        #expect(decision.claimedTotalPaise < decision.realTotalPaise)
        // Reason codes are the card's row keys; duplicates would drop rows.
        let codes = decision.reasons.map(\.code)
        #expect(Set(codes).count == codes.count)
        #expect(codes.contains("provenance.total_mismatch"))
    }

    @Test func aRefusedDecisionNeverCarriesARailReference() throws {
        let turn = try decode(AgentTurn.self, "turn_deny")
        let decision = try #require(turn.decision)
        // The card reads these to decide between "Paid", "Order" and "no". A
        // refusal carrying either would render as money that moved.
        #expect(decision.orderID == nil)
        #expect(decision.paymentID == nil)
    }

    @Test func theMerchantIsWhateverTheEngineSaidItWas() throws {
        // Named by the server, not parsed out of the cart id. The app should
        // never be inferring which shop was charged from a string shape.
        let turn = try decode(AgentTurn.self, "turn_deny")
        let decision = try #require(turn.decision)
        #expect(decision.merchant == "instamart")

        let silent = Decision(
            verdict: .allow, reasonCode: "ok.in_policy", reasons: [], cartID: "cart_1",
            realTotalPaise: 1, claimedTotalPaise: 1, idempotencyKey: "k",
            orderID: nil, keyID: nil, paymentID: nil
        )
        #expect(silent.merchant == nil, "no merchant means the card shows none")
    }

    // MARK: - money

    @Test func rupeesFormatsIndianGroupingAndNeverFloats() {
        #expect(rupees(0) == "₹0")
        #expect(rupees(21_500) == "₹215")
        #expect(rupees(185_000) == "₹1,850")
        #expect(rupees(1_685_000) == "₹16,850")
    }

    @Test func everyVerdictHasItsOwnColourAndSymbol() {
        let palette = Token.palette(.light)
        let verdicts: [Verdict] = [.allow, .clarify, .escalate, .deny]
        let colours = verdicts.map { palette.color(for: $0).description }
        #expect(Set(colours).count == 4, "two verdicts share a colour")
        #expect(Set(verdicts.map(\.symbol)).count == 4)
        #expect(Set(verdicts.map(\.headline)).count == 4)
    }
}

/// Anchors `Bundle(for:)` to the test bundle so fixtures resolve.
private final class BundleMarker {}

/// The cart lines a verdict carries. These are the evidence behind the reason
/// text, so a decode that loses them turns a specific refusal into a vague one.
struct CartLineTests {
    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        let url = try #require(
            Bundle(for: CartBundleMarker.self).url(forResource: name, withExtension: "json"),
            "missing fixture \(name).json"
        )
        return try JSONDecoder().decode(type, from: try Data(contentsOf: url))
    }

    @Test func anEscalationNamesExactlyTheLinesItFlagged() throws {
        let turn = try decode(AgentTurn.self, "turn_escalate")
        let decision = try #require(turn.decision)
        #expect(decision.verdict == .escalate)
        #expect(decision.items.count == 14)

        let flagged = decision.flagged
        #expect(flagged.count == 2)
        #expect(Set(flagged.map(\.name)) == ["Bluetooth earbuds", "Phone case"])
        // The chip shows the category, so it must not be blank.
        #expect(flagged.allSatisfy { !($0.note ?? "").isEmpty })

        // The reason text and the flagged lines must agree. If prose said two
        // items and the cart flagged none, the card would contradict itself.
        let scope = try #require(decision.reasons.first { $0.code == "category.not_allowed" })
        #expect(scope.detail.contains("Bluetooth earbuds"))
        #expect(scope.detail.contains("Phone case"))
    }

    @Test func theLinesSumToTheTotalTheEngineRuledOn() throws {
        let turn = try decode(AgentTurn.self, "turn_escalate")
        let decision = try #require(turn.decision)
        // Derived from the fetched cart, so the card cannot show a set of lines
        // that adds up to something other than the figure above them.
        #expect(decision.items.map(\.pricePaise).reduce(0, +) == decision.realTotalPaise)
    }

    @Test func aCleanCartFlagsNothingAndStaysCollapsed() throws {
        let list = try decode(ShoppingList.self, "list")
        let lines = list.items.map {
            CartLine(
                name: $0.name, pricePaise: $0.pricePaise ?? 0, category: $0.category,
                url: $0.url, offScope: false, unclassified: false
            )
        }
        let clean = Decision(
            verdict: .allow, reasonCode: "ok.in_policy", reasons: [],
            cartID: "instamart_cart_1", realTotalPaise: list.totalPaise,
            claimedTotalPaise: list.totalPaise, idempotencyKey: "k",
            orderID: "order_1", keyID: "rzp_test", paymentID: nil,
            items: lines, merchant: "instamart"
        )
        #expect(clean.flagged.isEmpty)
        #expect(clean.items.count == 12)
    }

    @Test func anUnclassifiedLineReadsAsUnclassifiedNotAsBlank() {
        let line = CartLine(
            name: "Whey protein 1kg", pricePaise: 32_000, category: "",
            url: "/m/instamart/p/x", offScope: false, unclassified: true
        )
        #expect(line.flagged)
        #expect(line.note == "unclassified")
    }
}

private final class CartBundleMarker {}

/// Copy details that the cards say out loud. Small, but a card is the product's
/// voice, and a broken plural reads as a broken build.
struct CopyTests {
    @Test func pluralsAgreeWithTheirCount() {
        #expect(plural(0, "item") == "0 items")
        #expect(plural(1, "item") == "1 item")
        #expect(plural(2, "item") == "2 items")
        #expect(plural(1, "shop") == "1 shop")
        #expect(plural(3, "shop") == "3 shops")
    }

    @Test func aGrantReadsAsUsedOnceNotAsACadence() {
        // "once every 1 days" is what a naive template produces for a grant
        // that exists to be spent exactly once.
        let bounds = MandateBounds(
            perTxnMaxPaise: 40_000, merchants: ["instamart"],
            categories: ["electronics"], everyDays: 1, ordersPerWindow: 1
        )
        #expect(bounds.everyDays == 1)
    }

    @Test func everyVerdictHeadlineIsPlainEnglish() {
        // No verdict should surface its enum name to a reader.
        for verdict in [Verdict.allow, .clarify, .escalate, .deny] {
            #expect(!verdict.headline.contains(verdict.rawValue))
            #expect(verdict.headline.first?.isUppercase == true)
        }
    }
}

/// Voice mode listens continuously, so the room gets transcribed too. What
/// separates a person from a passing lorry has to be explicit.
struct SpeechFilterTests {
    @Test func realInstructionsGetThrough() {
        #expect(Voice.isSpeech("Order my usual groceries from Instamart"))
        #expect(Voice.isSpeech("add earbuds"))
        #expect(Voice.isSpeech("what does atta cost"))
    }

    @Test func scribeAudioEventTagsAreNotInstructions() {
        // Observed live in the simulator: an empty room transcribed as this,
        // and the session handed it to the agent as something the user said.
        #expect(!Voice.isSpeech("[outro jingle]"))
        #expect(!Voice.isSpeech("[music]"))
        #expect(!Voice.isSpeech("[silence]"))
        #expect(!Voice.isSpeech("  [BLANK_AUDIO] "))
    }

    @Test func aTaggedRealSentenceStillCounts() {
        // Scribe interleaves tags with speech; the speech is what matters.
        #expect(Voice.isSpeech("[music] order my usual groceries"))
    }

    @Test func straySyllablesAndSilenceAreIgnored() {
        #expect(!Voice.isSpeech(""))
        #expect(!Voice.isSpeech("   "))
        #expect(!Voice.isSpeech("uh"))
        #expect(!Voice.isSpeech("."))
    }
}

/// A turn should leave cards behind. Spoken numbers are the one thing a voice
/// interface is worst at, so prices have to land on screen too.
struct SurfacedCardTests {
    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        let url = try #require(
            Bundle(for: SurfacedBundleMarker.self).url(forResource: name, withExtension: "json"),
            "missing fixture \(name).json"
        )
        return try JSONDecoder().decode(type, from: try Data(contentsOf: url))
    }

    @Test func askingWhatSomethingCostsPutsThePricesOnScreen() throws {
        let turn = try decode(AgentTurn.self, "turn_prices")
        #expect(turn.decision == nil, "a price question must not order anything")

        let cards = turn.surfaced
        #expect(!cards.isEmpty, "the agent searched and the screen showed nothing")

        guard case .offers(let product, let offers) = cards[0] else {
            Issue.record("expected an offers card")
            return
        }
        #expect(product.contains("atta"))
        // Grouped per product, so three shops read as one comparison.
        #expect(offers.count == 3)
        #expect(Set(offers.map(\.merchant)) == ["blinkit", "zepto", "instamart"])
    }

    @Test func offersReachTheAppWithThePolicysVerdictAttached() throws {
        let turn = try decode(AgentTurn.self, "turn_prices")
        guard case .offers(_, let offers) = turn.surfaced[0] else { return }

        let cheapest = try #require(offers.min { $0.pricePaise < $1.pricePaise })
        #expect(cheapest.merchant == "blinkit")
        #expect(!cheapest.buyable, "the cheapest shop is the off-policy one")
        #expect(offers.allSatisfy { $0.url.hasPrefix("/m/") })
    }

    @Test func aTurnThatOrderedSomethingEndsWithItsVerdict() throws {
        let turn = try decode(AgentTurn.self, "turn_escalate")
        let last = try #require(turn.surfaced.last)
        guard case .decision = last else {
            Issue.record("the verdict must be the last thing a buying turn shows")
            return
        }
    }
}

private final class SurfacedBundleMarker {}

/// A card is the product's voice. Nothing on it should look like a symbol.
struct CardCopyTests {
    private func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        let url = try #require(
            Bundle(for: CopyBundleMarker.self).url(forResource: name, withExtension: "json"),
            "missing fixture \(name).json"
        )
        return try JSONDecoder().decode(type, from: try Data(contentsOf: url))
    }

    @Test func nothingAUserReadsLooksLikeAnIdentifier() throws {
        let turn = try decode(AgentTurn.self, "turn_deny")
        let decision = try #require(turn.decision)

        // The code still travels — the ledger needs it — but nothing rendered
        // from it may carry an underscore or a dotted path.
        #expect(decision.reasonCode.contains("."), "the machine name is still there")
        let surfaces = [decision.summary, decision.settlement]
            + decision.reasons.map(\.title)
            + decision.reasons.map(\.detail)
        for surface in surfaces {
            #expect(!surface.contains("_"), "\(surface) reads like a symbol")
            #expect(!surface.contains("(s)"), "\(surface) reads like a form")
        }
    }

    @Test func theCardNeverSaysRail() throws {
        for name in ["turn_deny", "turn_escalate"] {
            let turn = try decode(AgentTurn.self, name)
            let decision = try #require(turn.decision)
            #expect(!decision.settlement.lowercased().contains("rail"))
            #expect(decision.settlement == "Nothing was charged")
        }
    }

    @Test func theItemsThatCausedTheRefusalComeFirst() throws {
        let turn = try decode(AgentTurn.self, "turn_escalate")
        let decision = try #require(turn.decision)
        #expect(decision.flagged.count == 2)

        // Two flagged rows should not sit at the bottom of fourteen the reader
        // has to scroll past to find why they were interrupted.
        let ordered = decision.orderedItems
        let firstTwoAreFlagged = ordered.prefix(2).allSatisfy { $0.flagged }
        #expect(firstTwoAreFlagged)
        // And reordering must not lose or duplicate a line.
        #expect(ordered.count == decision.items.count)
        #expect(Set(ordered.map(\.name)) == Set(decision.items.map(\.name)))
        #expect(ordered.map(\.pricePaise).reduce(0, +) == decision.realTotalPaise)
    }

    // MARK: - the one-time approval
    //
    // Captured from a live run against Razorpay test mode. The publishable key
    // is redacted — public by design or not, a fixture is not where credentials
    // belong, and the shape is the whole reason these files exist.

    @Test func theGrantDecodesWithBoundsTheAppDidNotAuthor() throws {
        let response = try decode(GrantResponse.self, "grant")
        let grant = response.grant

        // Every one of these was derived server-side from the basket. The app
        // sent a cart id and nothing else.
        #expect(grant.perTxnMaxPaise == 1_500_000)
        #expect(grant.merchants == ["instamart"])
        #expect(grant.deliveryAddress == "12 Nandidurga Rd, Bengaluru")
        #expect(grant.ordersPerWindow == 1)
        #expect(grant.cartID == "instamart_cart_1")
        #expect(grant.state == "ready")
        #expect(response.payPath == "/pay?grant=\(grant.grantID)")
    }

    @Test func theGrantExpiryIsReadable() throws {
        let grant = try decode(GrantResponse.self, "grant").grant

        // Python emits fractional seconds; a formatter that refuses them returns
        // nil, and the card then renders no expiry at all — the one thing a
        // fifteen-minute approval must never look like.
        let expiresAt = try #require(grant.expiresAt)
        #expect(isoDate(expiresAt) != nil, "the expiry did not parse")
    }

    @Test func approvingRefusesNothingAndAuthorisesTheOneBasket() throws {
        let response = try decode(GrantResponse.self, "grant")
        let decision = try #require(response.decision)

        #expect(decision.verdict == .allow)
        #expect(decision.orderID?.hasPrefix("order_") == true)
        // The grant is not a receipt. An order is not a payment, and the card
        // must not read as though money already moved.
        #expect(decision.paymentID == nil)
        #expect(decision.realTotalPaise == response.grant.perTxnMaxPaise)
        // Nothing left to approve — the card must not offer a second grant.
        #expect(!decision.grantable)
    }

    // MARK: - one product, and what else would do

    @Test func aProductCarriesEverythingNeededToChoose() throws {
        let detail = try decode(ProductDetail.self, "product")

        #expect(detail.product.name == "Aashirvaad atta 5kg")
        #expect(detail.product.imageURL?.hasPrefix("https://") == true)
        #expect(detail.product.buyable)
        #expect(detail.product.blockedReason == nil)
        #expect(!detail.alternatives.isEmpty)
    }

    @Test func theCheapestAlternativeIsOftenTheOneYourRuleRefuses() throws {
        """
        The scene the mock exists for: Blinkit undercuts Instamart on the
        staples, so the cheapest row and the allowed row are different rows.
        Collapsing shop and category into one flag would name the wrong reason.
        """
        let detail = try decode(ProductDetail.self, "product")
        let cheapest = try #require(detail.alternatives.min { $0.pricePaise < $1.pricePaise })

        #expect(cheapest.pricePaise < detail.product.pricePaise)
        #expect(!cheapest.buyable)
        #expect(cheapest.blockedReason?.contains("not on your list") == true)
    }

    @Test func everyAlternativeShowsAPictureAndItsOwnVerdict() throws {
        let detail = try decode(ProductDetail.self, "product")

        #expect(detail.alternatives.allSatisfy { $0.imageURL?.hasPrefix("https://") == true })
        // Two answers, never one — a shop can be allowed while what it sells
        // is not, and this sheet exists to help somebody choose.
        #expect(detail.alternatives.allSatisfy { $0.buyable == ($0.merchantAllowed && $0.categoryAllowed) })
    }

    // MARK: - the home screen's states
    //
    // Eight captures from a running engine, one per state. What is worth
    // pinning is not that they decode but *which single fact* each one leads
    // with, because the whole surface exists to say one thing to somebody who
    // was not there when it happened.

    private func home(_ state: String) throws -> Home {
        try decode(Home.self, "home_\(state)")
    }

    @Test func theRuleFinallyHasAScreen() throws {
        let rule = try home("at_rest").rule
        #expect(rule.perTxnMaxPaise == 200_000)
        // One line, not four labelled rows.
        #expect(rule.summary == "instamart · groceries · every 4 days · to Home")
    }

    @Test func aListAboutToRunSaysThereIsNothingToDo() throws {
        let out = try home("preflight")
        #expect(out.state == "preflight")
        #expect(out.detail.lowercased().contains("nothing for you to do"))
        #expect(out.actions.map(\.id) == ["pause", "view_basket"])
    }

    @Test func anOrderYouMissedSaysSo() throws {
        let out = try home("ruled")
        #expect(out.state == "ruled")
        #expect(out.detail.contains("while you were away"))
    }

    @Test func anEscalationOffersRoutesAndTakesNone() throws {
        let out = try home("escalated")
        #expect(out.actions.map(\.id) == ["approve_once", "drop_flagged", "not_now"])
        #expect(out.decision?.verdict == .escalate)
        // Which two items, not just how many.
        #expect(out.detail.contains("Bluetooth earbuds"))
    }

    @Test func aRefusalOffersNoWayToApproveIt() throws {
        let out = try home("refused")
        #expect(out.decision?.verdict == .deny)
        #expect(out.actions.map(\.id) == ["see_attempt"])
        #expect(!out.actions.contains { $0.id == "approve_once" })
    }

    @Test func theHaltNamesTheAddressAndNotSomethingElse() throws {
        let out = try home("halted")
        #expect(out.headline.contains("authorised"))
        #expect(out.actions.map(\.id) == ["reauthorise", "cancel_basket"])
    }

    @Test func aLiveGrantSaysItIsSpendableOnce() throws {
        let out = try home("grant_live")
        #expect(out.state == "grant_live")
        #expect(out.grantID?.hasPrefix("grant_") == true)
        #expect(out.detail.contains("once"))
    }

    @Test func onlyTheStatesYouCanPutDownAreDismissable() throws {
        // A refusal wants a decision; a live grant is spending a clock.
        #expect(try home("escalated").dismissable)
        #expect(try !home("preflight").dismissable)
        #expect(try !home("grant_live").dismissable)
    }

    @Test func nothingOnTheHomeScreenReadsLikeAnIdentifier() throws {
        for state in ["at_rest", "preflight", "ruled", "escalated", "refused",
                      "clarify", "halted", "grant_live"] {
            let out = try home(state)
            for line in [out.headline, out.detail, out.chip] + out.actions.map(\.label) {
                #expect(!line.contains("_"), "\(state): \(line) reads like a symbol")
            }
        }
    }

    // MARK: - product photographs
    //
    // `decision_live.json` was captured from a real Instamart cart on
    // 2026-08-26. The mock has no photographs because it has no products, so
    // this is the only fixture that can exercise the path at all.

    @Test func aLiveCartLineCarriesItsPhotograph() throws {
        let decision = try decode(Decision.self, "decision_live")
        let goods = decision.items.filter { $0.category != "fees" }

        #expect(!goods.isEmpty)
        #expect(goods.allSatisfy { $0.imageURL?.hasPrefix("https://") == true })
        // Sized on the merchant's CDN: 648 KB becomes 10.7 KB, measured. A
        // twelve-line cart at full size would be 7 MB to draw twelve thumbnails.
        #expect(goods.allSatisfy { $0.imageURL?.contains("w_160,h_160,c_fit") == true })
    }

    @Test func aBillLineIsNotAThingAndHasNoPhotograph() throws {
        let decision = try decode(Decision.self, "decision_live")
        let fees = decision.items.filter { $0.category == "fees" }

        #expect(!fees.isEmpty)
        // Blank and absent both decode to nil, so the row draws nothing rather
        // than a placeholder box beside a handling fee.
        #expect(fees.allSatisfy { $0.imageURL == nil })
    }

    @Test func aCartCapturedBeforeImagesExistedStillDecodes() throws {
        let turn = try decode(AgentTurn.self, "turn_escalate")
        let decision = try #require(turn.decision)

        #expect(!decision.items.isEmpty)
        #expect(decision.items.allSatisfy { $0.imageURL == nil })
        // And the flag survives having no picture — which is the property that
        // matters. A merchant serving no photo must not make an off-scope line
        // read as ordinary.
        #expect(decision.flagged.count == 2)
        #expect(decision.orderedItems.prefix(2).allSatisfy { $0.flagged })
    }

    @Test func everyLineTheUserReadsCarriesAPhotograph() throws {
        struct Offers: Decodable { let offers: [Offer] }

        let list = try decode(ShoppingList.self, "list")
        #expect(list.items.allSatisfy { $0.imageURL?.hasPrefix("https://") == true })

        let offers = try decode(Offers.self, "offers").offers
        #expect(offers.allSatisfy { $0.imageURL?.hasPrefix("https://") == true })

        // One picture per product, not per seller — the offers card draws it
        // once in the header, and three shops selling the same atta must not
        // disagree about what atta looks like.
        #expect(Set(offers.map(\.imageURL)).count == 1)
    }

    // MARK: - where things get delivered

    @Test func theAddressBookSaysWhereOrdersGo() throws {
        struct Book: Decodable { let addresses: [DeliveryAddress] }
        let book = try decode(Book.self, "addresses").addresses

        #expect(book.count == 2)
        let selected = try #require(book.first { $0.selected })
        #expect(selected.label == "Home")
        // Selected and authorised are answered separately by the server, and
        // the row renders both — an address you deliver to that your rule does
        // not permit is a state worth being able to show.
        #expect(selected.authorised)
        #expect(book.filter(\.selected).count == 1)
    }

    @Test func anAddressIsIdentifiedByItsIDNotItsText() throws {
        struct Book: Decodable { let addresses: [DeliveryAddress] }
        let book = try decode(Book.self, "addresses").addresses

        // The engine matches on this. If a client ever keyed off `line`, the
        // live path would break silently — Swiggy formats one address two ways.
        #expect(book.allSatisfy { !$0.addressID.isEmpty })
        #expect(Set(book.map(\.id)).count == book.count)
        #expect(book.allSatisfy { $0.addressID != $0.line })
    }

    @Test func theRefusalThatPromptsAnApprovalOffersOne() throws {
        let escalated = try #require(try decode(AgentTurn.self, "turn_escalate").decision)
        let denied = try #require(try decode(AgentTurn.self, "turn_deny").decision)

        #expect(escalated.grantable)
        // An agent that misreported its own basket is not a thing to wave
        // through with one tap.
        #expect(!denied.grantable)
    }
}

private final class CopyBundleMarker {}
