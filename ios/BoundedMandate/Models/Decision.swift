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
    let code: String
    let detail: String
}

/// One line of the cart the engine actually fetched — not the one the agent
/// described. When a verdict says "2 items outside your scope", the reader
/// should be able to see which two rather than take the sentence on trust.
struct CartLine: Codable, Hashable, Sendable, Identifiable {
    let name: String
    let pricePaise: Int
    let category: String
    let url: String
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
        case offScope = "off_scope"
        case unclassified
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

    var id: String { idempotencyKey + reasonCode }
    var flagged: [CartLine] { items.filter(\.flagged) }

    /// The agent misreported its own cart. The engine caught it by refetching.
    var lied: Bool { claimedTotalPaise != realTotalPaise }

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
        case items, merchant
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
    }

    init(
        verdict: Verdict, reasonCode: String, reasons: [Reason], cartID: String,
        realTotalPaise: Int, claimedTotalPaise: Int, idempotencyKey: String,
        orderID: String?, keyID: String?, paymentID: String?,
        items: [CartLine] = [], merchant: String? = nil
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
    }
}

struct AgentTurn: Codable, Sendable {
    let said: String
    let decision: Decision?
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
