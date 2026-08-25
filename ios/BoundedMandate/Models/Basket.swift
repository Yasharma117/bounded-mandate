import Foundation

/// One line of the user's list, priced at the shop their mandate allows.
struct ListItem: Codable, Hashable, Sendable, Identifiable {
    let name: String
    let pricePaise: Int?
    let category: String
    let url: String

    var id: String { name }
    /// The shop does not stock it. Shown, not silently dropped — a list is a
    /// source of truth, and quietly losing a line would make it a bad one.
    var unstocked: Bool { pricePaise == nil }

    enum CodingKeys: String, CodingKey {
        case name
        case pricePaise = "price_paise"
        case category, url
    }
}

/// The user's shopping list: what "my usual groceries" actually means.
///
/// This is the second thing the user owns and it is not the policy. The policy
/// bounds *how much*; the list defines *what*. The agent reads it and has no
/// tool that can write it.
struct ShoppingList: Codable, Hashable, Sendable, Identifiable {
    let listID: String
    let name: String
    let merchant: String
    let items: [ListItem]
    let totalPaise: Int
    let capPaise: Int
    let unstocked: [String]

    /// `standing` repeats; `once` runs on a date and is then spent.
    let kind: String
    let everyDays: Int?
    let paused: Bool
    let spent: Bool
    let due: Bool
    let lastRunAt: String?
    let nextDueAt: String?
    /// When this runs, phrased server-side — "Every 4 days", "Once, on 8 Nov".
    let schedule: String

    var id: String { listID }
    var isOneOff: Bool { kind == "once" }

    /// How much of the per-order cap this list would consume. Over 1 means the
    /// list as written cannot clear the policy, which the user should see
    /// before an agent run tells them.
    var capUsed: Double {
        guard capPaise > 0 else { return 0 }
        return Double(totalPaise) / Double(capPaise)
    }

    var overCap: Bool { totalPaise > capPaise }
    var headroomPaise: Int { capPaise - totalPaise }

    enum CodingKeys: String, CodingKey {
        case listID = "list_id"
        case name, merchant, items, kind, paused, spent, due, schedule, unstocked
        case totalPaise = "total_paise"
        case capPaise = "cap_paise"
        case everyDays = "every_days"
        case lastRunAt = "last_run_at"
        case nextDueAt = "next_due_at"
    }

    init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        listID = try box.decode(String.self, forKey: .listID)
        name = try box.decode(String.self, forKey: .name)
        merchant = try box.decode(String.self, forKey: .merchant)
        items = try box.decode([ListItem].self, forKey: .items)
        totalPaise = try box.decode(Int.self, forKey: .totalPaise)
        capPaise = try box.decode(Int.self, forKey: .capPaise)
        unstocked = try box.decodeIfPresent([String].self, forKey: .unstocked) ?? []
        kind = try box.decodeIfPresent(String.self, forKey: .kind) ?? "standing"
        everyDays = try box.decodeIfPresent(Int.self, forKey: .everyDays)
        paused = try box.decodeIfPresent(Bool.self, forKey: .paused) ?? false
        spent = try box.decodeIfPresent(Bool.self, forKey: .spent) ?? false
        due = try box.decodeIfPresent(Bool.self, forKey: .due) ?? false
        lastRunAt = try box.decodeIfPresent(String.self, forKey: .lastRunAt)
        nextDueAt = try box.decodeIfPresent(String.self, forKey: .nextDueAt)
        schedule = try box.decodeIfPresent(String.self, forKey: .schedule) ?? ""
    }

    init(
        listID: String, name: String, merchant: String, items: [ListItem],
        totalPaise: Int, capPaise: Int, unstocked: [String],
        kind: String = "standing", everyDays: Int? = nil, paused: Bool = false,
        spent: Bool = false, due: Bool = false, lastRunAt: String? = nil,
        nextDueAt: String? = nil, schedule: String = ""
    ) {
        self.listID = listID
        self.name = name
        self.merchant = merchant
        self.items = items
        self.totalPaise = totalPaise
        self.capPaise = capPaise
        self.unstocked = unstocked
        self.kind = kind
        self.everyDays = everyDays
        self.paused = paused
        self.spent = spent
        self.due = due
        self.lastRunAt = lastRunAt
        self.nextDueAt = nextDueAt
        self.schedule = schedule
    }
}

/// One shop's price for one product, with the engine's verdict on the shop.
struct Offer: Codable, Hashable, Sendable, Identifiable {
    let merchant: String
    let name: String
    let pricePaise: Int
    let category: String
    let url: String
    /// Computed server-side. Whether a shop is allowed is the engine's
    /// judgement, and the app should not be reimplementing it.
    ///
    /// Two answers rather than one: a shop can be allowed while the thing it
    /// sells is not, and collapsing them would make the card name the wrong
    /// reason — which is worse than naming none.
    let merchantAllowed: Bool
    let categoryAllowed: Bool

    var id: String { merchant + name }
    var buyable: Bool { merchantAllowed && categoryAllowed }

    /// Why this offer is out of scope, in the user's terms.
    var blockedReason: String? {
        switch (merchantAllowed, categoryAllowed) {
        case (true, true): nil
        case (false, true): "shop not on your list"
        case (true, false): "\(category) not in your rule"
        case (false, false): "shop and \(category) both out of scope"
        }
    }

    enum CodingKeys: String, CodingKey {
        case merchant, name, category, url
        case pricePaise = "price_paise"
        case merchantAllowed = "merchant_allowed"
        case categoryAllowed = "category_allowed"
    }
}

/// The standing mandate, as bounds rather than prose.
struct MandateBounds: Hashable, Sendable {
    let perTxnMaxPaise: Int
    let merchants: [String]
    let categories: [String]
    let everyDays: Int
    let ordersPerWindow: Int
}
