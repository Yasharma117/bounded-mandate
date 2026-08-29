import SwiftUI

/// The standing rule, finally served.
///
/// It is the central object of the whole product and had no route and no screen
/// until this — the mandate card existed, but only a one-time grant ever reached
/// it.
struct Rule: Decodable, Hashable, Sendable {
    let perTxnMaxPaise: Int
    let merchants: [String]
    let categories: [String]
    let everyDays: Int
    let ordersPerWindow: Int
    let status: String
    let delivery: Delivery?

    struct Delivery: Decodable, Hashable, Sendable {
        let label: String
        let line: String
    }

    /// `Instamart · groceries · every 4 days · to Office`, as one line.
    ///
    /// A row of labelled fields would be four times the height and say the same
    /// thing; a rule is short enough to be a sentence.
    var summary: String {
        var parts = merchants + categories
        parts.append(everyDays == 1 ? "every day" : "every \(everyDays) days")
        if let delivery { parts.append("to \(delivery.label)") }
        return parts.joined(separator: " · ")
    }

    var bounds: MandateBounds {
        MandateBounds(
            perTxnMaxPaise: perTxnMaxPaise, merchants: merchants, categories: categories,
            everyDays: everyDays, ordersPerWindow: ordersPerWindow
        )
    }

    enum CodingKeys: String, CodingKey {
        case merchants, categories, status, delivery
        case perTxnMaxPaise = "per_txn_max_paise"
        case everyDays = "every_days"
        case ordersPerWindow = "orders_per_window"
    }
}

/// One route the engine is offering. **Proposed, never taken** — the same
/// contract it keeps with the agent, rendered as UI.
struct HomeAction: Decodable, Hashable, Sendable, Identifiable {
    let id: String
    let label: String
}

/// A decision as the *ledger* stored it, which is not the shape a decision card
/// receives. There is no fetched cart here and no settlement — only what was
/// written down at the moment it was decided.
struct LedgerDecision: Decodable, Hashable, Sendable {
    let verdict: Verdict
    let reasonCode: String
    let totalPaise: Int
    let idempotencyKey: String
    let cartID: String?
    let reasons: [Reason]
    /// The basket the **engine fetched**, not the one the agent described.
    /// Saying "₹1,850, inside your rule" without showing what it bought is
    /// asking to be taken on trust, which is the one thing this product does
    /// not do.
    let items: [CartLine]

    /// Flagged lines first — the two items that caused a refusal should not be
    /// the two you have to scroll to.
    var ordered: [CartLine] { items.filter(\.flagged) + items.filter { !$0.flagged } }

    /// Fees are a charge on the delivery, not a thing anyone chose. They belong
    /// in the total and not in a row of product pictures.
    var goods: [CartLine] { ordered.filter { $0.category != "fees" } }

    enum CodingKeys: String, CodingKey {
        case verdict, reasons, items
        case reasonCode = "reason_code"
        case totalPaise = "total_paise"
        case idempotencyKey = "idempotency_key"
        case cartID = "cart_id"
    }

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        verdict = try box.decode(Verdict.self, forKey: .verdict)
        reasonCode = try box.decodeIfPresent(String.self, forKey: .reasonCode) ?? ""
        totalPaise = try box.decodeIfPresent(Int.self, forKey: .totalPaise) ?? 0
        idempotencyKey = try box.decodeIfPresent(String.self, forKey: .idempotencyKey) ?? ""
        cartID = try box.decodeIfPresent(String.self, forKey: .cartID)
        reasons = try box.decodeIfPresent([Reason].self, forKey: .reasons) ?? []
        items = try box.decodeIfPresent([CartLine].self, forKey: .items) ?? []
    }
}

/// One line of the audit trail, as the home screen shows it.
struct LedgerRow: Decodable, Hashable, Sendable, Identifiable {
    let ts: String
    let verdict: Verdict?
    let summary: String
    let totalPaise: Int?
    let event: String?

    var id: String { ts + (verdict?.rawValue ?? event ?? "") }

    /// `SETTLED`, `SEEN` and `HALTED` carry no verdict — they are things that
    /// happened *around* a decision rather than one.
    var label: String {
        if let event { return event.capitalized }
        return verdict?.headline ?? ""
    }

    enum CodingKeys: String, CodingKey {
        case ts, verdict, summary, event
        case totalPaise = "total_paise"
    }
}

/// Where do I stand — answered before anybody asks.
struct Home: Decodable, Sendable {
    let rule: Rule
    /// `at_rest` · `preflight` · `ruled` · `needs_you` · `grant_live`.
    /// **Decided server-side**, like every other judgement in this app.
    let state: String
    let chip: String
    let headline: String
    let detail: String
    let actions: [HomeAction]
    let decision: LedgerDecision?
    let grantID: String?
    let listID: String?
    let lists: [ShoppingList]
    let chainIntact: Bool
    let recent: [LedgerRow]

    /// The colour of the state. Presentation, so it is decided here — but keyed
    /// off the server's `state` so the two cannot disagree about *which* state.
    func tint(_ theme: Token.Palette) -> Color {
        switch state {
        case "needs_you": decision.map { theme.color(for: $0.verdict) } ?? theme.notice
        case "grant_live": theme.orchid
        default: theme.primary
        }
    }

    /// Whether this state is one the reader can put down. A refusal and a live
    /// grant are not — one wants a decision, the other is spending a clock.
    var dismissable: Bool { state == "needs_you" && decision != nil }

    enum CodingKeys: String, CodingKey {
        case rule, state, chip, headline, detail, actions, decision, lists, recent
        case grantID = "grant_id"
        case listID = "list_id"
        case chainIntact = "chain_intact"
    }
}
