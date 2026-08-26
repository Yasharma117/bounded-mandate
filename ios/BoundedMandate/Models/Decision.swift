import Foundation

enum Verdict: String, Codable, Sendable {
    case allow = "ALLOW"
    case clarify = "CLARIFY"
    case escalate = "ESCALATE"
    case deny = "DENY"

    /// The headline the card leads with. The verdict, in the user's words.
    var headline: String {
        switch self {
        case .allow: "Authorised"
        case .clarify: "Needs an answer"
        case .escalate: "Your call"
        case .deny: "Refused"
        }
    }

    var symbol: String {
        switch self {
        case .allow: "checkmark.seal.fill"
        case .clarify: "questionmark.circle.fill"
        case .escalate: "exclamationmark.triangle.fill"
        case .deny: "hand.raised.fill"
        }
    }
}

struct Reason: Codable, Hashable, Sendable {
    /// What the ledger stores. Never shown to a user.
    let code: String
    /// The same thing in words, decided server-side so the machine name and
    /// the human one cannot drift apart in a client nobody updated.
    let title: String
    let detail: String

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        code = try box.decode(String.self, forKey: .code)
        detail = try box.decode(String.self, forKey: .detail)
        title = try box.decodeIfPresent(String.self, forKey: .title) ?? code
    }

    init(code: String, title: String, detail: String) {
        self.code = code
        self.title = title
        self.detail = detail
    }
}

/// One line of the cart the engine actually fetched — not the one the agent
/// described. When a verdict says "2 items outside your scope", the reader
/// should be able to see which two rather than take the sentence on trust.
struct CartLine: Codable, Hashable, Sendable, Identifiable {
    let name: String
    let pricePaise: Int
    let category: String
    let url: String
    /// A picture of the thing, when the merchant has one. **Decoration.**
    ///
    /// The mock has no photographs because it has no products, so this is
    /// absent on the offline path and the row renders exactly as it did before
    /// — a column of empty placeholders would be worse than no column.
    let imageURL: String?
    /// The policy's judgement, computed server-side.
    let offScope: Bool
    let unclassified: Bool

    var id: String { name }
    var flagged: Bool { offScope || unclassified }

    var note: String? {
        if offScope { return category }
        if unclassified { return "unclassified" }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case name, category, url
        case pricePaise = "price_paise"
        case imageURL = "image_url"
        case offScope = "off_scope"
        case unclassified
    }

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        name = try box.decode(String.self, forKey: .name)
        category = try box.decode(String.self, forKey: .category)
        url = try box.decode(String.self, forKey: .url)
        pricePaise = try box.decode(Int.self, forKey: .pricePaise)
        offScope = try box.decode(Bool.self, forKey: .offScope)
        unclassified = try box.decode(Bool.self, forKey: .unclassified)
        // Blank and absent are the same thing: no picture. Payloads captured
        // before this field existed still decode.
        let image = try box.decodeIfPresent(String.self, forKey: .imageURL)
        imageURL = (image?.isEmpty ?? true) ? nil : image
    }

    init(
        name: String, pricePaise: Int, category: String, url: String,
        imageURL: String? = nil, offScope: Bool = false, unclassified: Bool = false
    ) {
        self.name = name
        self.pricePaise = pricePaise
        self.category = category
        self.url = url
        self.imageURL = imageURL
        self.offScope = offScope
        self.unclassified = unclassified
    }
}

struct Decision: Codable, Hashable, Sendable, Identifiable {
    let verdict: Verdict
    let reasonCode: String
    let reasons: [Reason]
    let cartID: String
    let realTotalPaise: Int
    let claimedTotalPaise: Int
    let idempotencyKey: String
    let orderID: String?
    let keyID: String?
    /// Set only once a checkout has actually captured — an order is not a payment.
    let paymentID: String?
    /// Defaulted so older captured payloads still decode.
    let items: [CartLine]
    let merchant: String?

    /// The same verdict in words. `category.not_allowed+cap.exceeded` is right
    /// for the ledger and wrong for someone who just wanted groceries.
    let summary: String
    /// What happened to the money, in plain words. "Reached the rail" is our
    /// vocabulary, not theirs.
    let settlement: String

    var id: String { idempotencyKey + reasonCode }
    var flagged: [CartLine] { items.filter(\.flagged) }

    /// Flagged lines first. The two items that caused a refusal should not be
    /// at the bottom of fourteen rows the reader has to scroll past to find
    /// the answer to the question the card just raised.
    var orderedItems: [CartLine] {
        items.filter(\.flagged) + items.filter { !$0.flagged }
    }

    /// The agent misreported its own cart. The engine caught it by refetching.
    var lied: Bool { claimedTotalPaise != realTotalPaise }

    /// Whether to offer a one-time approval.
    ///
    /// An ALLOW needs nothing. A DENY is deliberately not offered: an agent
    /// that misreported its own basket, or one already caught testing the
    /// fence, is not a thing to wave through with one tap. What is left is the
    /// refusal a person can overrule by *looking* — which is what the approval
    /// makes them do.
    var grantable: Bool { verdict == .escalate || verdict == .clarify }

    enum CodingKeys: String, CodingKey {
        case verdict
        case reasonCode = "reason_code"
        case reasons
        case cartID = "cart_id"
        case realTotalPaise = "real_total_paise"
        case claimedTotalPaise = "claimed_total_paise"
        case idempotencyKey = "idempotency_key"
        case orderID = "order_id"
        case keyID = "key_id"
        case paymentID = "payment_id"
        case items, merchant, summary, settlement
    }

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        verdict = try box.decode(Verdict.self, forKey: .verdict)
        reasonCode = try box.decode(String.self, forKey: .reasonCode)
        reasons = try box.decode([Reason].self, forKey: .reasons)
        cartID = try box.decode(String.self, forKey: .cartID)
        realTotalPaise = try box.decode(Int.self, forKey: .realTotalPaise)
        claimedTotalPaise = try box.decode(Int.self, forKey: .claimedTotalPaise)
        idempotencyKey = try box.decode(String.self, forKey: .idempotencyKey)
        orderID = try box.decodeIfPresent(String.self, forKey: .orderID)
        keyID = try box.decodeIfPresent(String.self, forKey: .keyID)
        paymentID = try box.decodeIfPresent(String.self, forKey: .paymentID)
        items = try box.decodeIfPresent([CartLine].self, forKey: .items) ?? []
        merchant = try box.decodeIfPresent(String.self, forKey: .merchant)
        summary = try box.decodeIfPresent(String.self, forKey: .summary) ?? ""
        settlement = try box.decodeIfPresent(String.self, forKey: .settlement) ?? ""
    }

    init(
        verdict: Verdict, reasonCode: String, reasons: [Reason], cartID: String,
        realTotalPaise: Int, claimedTotalPaise: Int, idempotencyKey: String,
        orderID: String?, keyID: String?, paymentID: String?,
        items: [CartLine] = [], merchant: String? = nil,
        summary: String = "", settlement: String = ""
    ) {
        self.verdict = verdict
        self.reasonCode = reasonCode
        self.reasons = reasons
        self.cartID = cartID
        self.realTotalPaise = realTotalPaise
        self.claimedTotalPaise = claimedTotalPaise
        self.idempotencyKey = idempotencyKey
        self.orderID = orderID
        self.keyID = keyID
        self.paymentID = paymentID
        self.items = items
        self.merchant = merchant
        self.summary = summary
        self.settlement = settlement
    }
}

/// One tool the agent reached for, and what came back.
struct AgentStep: Codable, Sendable {
    let tool: String
    let result: StepResult

    struct StepResult: Codable, Sendable {
        /// Present on `search_catalog`, annotated with the policy's verdict on
        /// the way out — the model itself saw these bare.
        let offers: [Offer]?
        /// Present on `read_shopping_list`.
        let itemNames: [String]?

        enum CodingKeys: String, CodingKey {
            case offers
            case itemNames = "item_names"
        }
    }
}

struct AgentTurn: Codable, Sendable {
    let said: String
    let decision: Decision?
    let steps: [AgentStep]

    /// Cards this turn earned, in the order they were earned.
    ///
    /// A conversation should leave things behind. Asking what something costs
    /// should put the prices on screen, not only say them — spoken numbers are
    /// the one thing a voice interface is worst at.
    var surfaced: [Surfaced] {
        var cards: [Surfaced] = []
        for step in steps {
            guard let offers = step.result.offers, !offers.isEmpty else { continue }
            // One card per product, so three shops for one item read as a
            // comparison rather than as three unrelated results.
            for (product, group) in Dictionary(grouping: offers, by: \.name)
                .sorted(by: { $0.key < $1.key }) {
                cards.append(.offers(product: product, offers: group))
            }
        }
        if let decision { cards.append(.decision(decision)) }
        return cards
    }

    enum Surfaced: Identifiable {
        case offers(product: String, offers: [Offer])
        case decision(Decision)

        var id: String {
            switch self {
            case .offers(let product, _): "offers-" + product
            case .decision(let decision): "decision-" + decision.id
            }
        }
    }

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        said = try box.decode(String.self, forKey: .said)
        decision = try box.decodeIfPresent(Decision.self, forKey: .decision)
        steps = try box.decodeIfPresent([AgentStep].self, forKey: .steps) ?? []
    }

    enum CodingKeys: String, CodingKey { case said, decision, steps }
}

/// "1 item", not "1 items". Small, but the card is the product's voice and a
/// broken plural reads as a broken build.
func plural(_ count: Int, _ singular: String, _ many: String? = nil) -> String {
    "\(count) " + (count == 1 ? singular : (many ?? singular + "s"))
}

/// Integer paise throughout, never floats — the same rule the engine keeps.
func rupees(_ paise: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.locale = Locale(identifier: "en_IN")
    formatter.maximumFractionDigits = 0
    let value = NSNumber(value: Double(paise) / 100)
    return "₹" + (formatter.string(from: value) ?? "\(paise / 100)")
}
