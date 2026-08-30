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

/// What the engine has actually done, counted off the ledger it already keeps.
///
/// Every figure is derived from decision entries written at the time — nothing
/// here is recorded for the purpose of being counted. That is the point: a
/// number kept in its own tally could drift without the chain noticing, and
/// these cannot, because they *are* the chain.
struct Stats: Decodable, Sendable {
    let decisions: Int
    let allowed: Int
    let refused: Int
    let authorisedPaise: Int
    /// Money an autonomous agent asked for and did not get. The number the
    /// product exists to produce.
    let heldBackPaise: Int
    /// Why something was refused, not merely that it was — keyed by a phrase
    /// already written for a reader. Zero counts are absent, not shown as zero.
    let blocked: [String: Int]
    let settlements: Int

    /// Most-blocked first, then alphabetically so the order is stable between
    /// refreshes rather than jumping as counts tie.
    var reasons: [(label: String, count: Int)] {
        blocked.sorted { ($0.value, $1.key) > ($1.value, $0.key) }
            .map { (label: $0.key, count: $0.value) }
    }

    enum CodingKeys: String, CodingKey {
        case decisions, allowed, refused, blocked, settlements
        case authorisedPaise = "authorised_paise"
        case heldBackPaise = "held_back_paise"
    }
}
