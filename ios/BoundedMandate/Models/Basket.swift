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
struct ShoppingList: Codable, Hashable, Sendable {
    let listID: String
    let name: String
    let merchant: String
    let items: [ListItem]
    let totalPaise: Int
    let capPaise: Int
    let unstocked: [String]

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
        case name, merchant, items
        case totalPaise = "total_paise"
        case capPaise = "cap_paise"
        case unstocked
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
