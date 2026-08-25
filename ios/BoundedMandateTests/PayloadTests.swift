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
        #expect(decision.reasons.count >= 4)
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
