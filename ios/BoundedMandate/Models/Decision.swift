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

    var id: String { idempotencyKey + reasonCode }

    /// The agent misreported its own cart. The engine caught it by refetching.
    var lied: Bool { claimedTotalPaise != realTotalPaise }

    /// Cart ids become merchant-prefixed when the marketplace lands. Until
    /// then there is no seller to name, and naming one anyway would be the
    /// app asserting something the engine never told it.
    var merchant: String? {
        let prefix = cartID.split(separator: "_").first.map(String.init) ?? ""
        return prefix == "cart" ? nil : prefix
    }

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
    }
}

struct AgentTurn: Codable, Sendable {
    let said: String
    let decision: Decision?
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
