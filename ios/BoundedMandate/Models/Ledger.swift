import Foundation

/// One line of the audit trail, as the ledger stored it.
///
/// Entries are not all the same shape: a decision carries a verdict and a
/// reason code, a settlement carries Razorpay's references, a dismissal carries
/// only the key it dismissed. Decoded loosely on purpose — a ledger that
/// refuses to render because a future entry grew a field would be a poor
/// audit trail.
struct LedgerEntry: Decodable, Hashable, Sendable, Identifiable {
    let seq: Int
    let ts: String
    let verdict: Verdict?
    let reasonCode: String?
    let totalPaise: Int?
    let event: String?
    let paymentID: String?

    var id: Int { seq }

    /// What happened, in the user's words rather than the ledger's.
    var headline: String {
        if let event {
            switch event {
            case "SETTLED": return "Paid"
            case "SEEN": return "You looked at it"
            case "HALTED": return "Halted"
            default: return event.capitalized
            }
        }
        return verdict?.headline ?? "—"
    }

    enum CodingKeys: String, CodingKey {
        case seq, ts, verdict, event
        case reasonCode = "reason_code"
        case totalPaise = "total_paise"
        case paymentID = "razorpay_payment_id"
    }

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        seq = try box.decode(Int.self, forKey: .seq)
        ts = try box.decode(String.self, forKey: .ts)
        verdict = try? box.decodeIfPresent(Verdict.self, forKey: .verdict)
        reasonCode = try box.decodeIfPresent(String.self, forKey: .reasonCode)
        totalPaise = try box.decodeIfPresent(Int.self, forKey: .totalPaise)
        event = try box.decodeIfPresent(String.self, forKey: .event)
        paymentID = try box.decodeIfPresent(String.self, forKey: .paymentID)
    }
}

struct LedgerPage: Decodable, Sendable {
    /// Whether the hash chain still verifies. This is the whole claim: an
    /// append-only ledger you can *check* rather than one you are asked to
    /// believe.
    let chainIntact: Bool
    let entries: [LedgerEntry]

    enum CodingKeys: String, CodingKey {
        case entries
        case chainIntact = "chain_intact"
    }
}
