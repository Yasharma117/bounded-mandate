import Foundation

/// One product, and what else would do instead.
///
/// A line on a list is a name and a price — enough to decide *whether*, not
/// enough to decide *which*. This is the answer to the second question, and
/// every row in it carries the policy's verdict, because an alternative the
/// rule does not cover is a thing to learn now rather than after an escalation.
struct Product: Decodable, Hashable, Sendable, Identifiable {
    let merchant: String
    let name: String
    let pricePaise: Int
    let category: String
    let imageURL: String?
    let url: String
    /// Two answers, never one. A shop can be allowed while the thing it sells
    /// is not, and collapsing them makes the sheet name the wrong reason on the
    /// one screen somebody opened in order to choose.
    let merchantAllowed: Bool
    let categoryAllowed: Bool

    var id: String { merchant + name }
    var buyable: Bool { merchantAllowed && categoryAllowed }

    var blockedReason: String? {
        switch (merchantAllowed, categoryAllowed) {
        case (true, true): nil
        case (false, true): "\(merchant) is not on your list"
        case (true, false): "\(category) is not in your rule"
        case (false, false): "\(merchant) and \(category) are both out of scope"
        }
    }

    enum CodingKeys: String, CodingKey {
        case merchant, name, category, url
        case pricePaise = "price_paise"
        case imageURL = "image_url"
        case merchantAllowed = "merchant_allowed"
        case categoryAllowed = "category_allowed"
    }
}

struct ProductDetail: Decodable, Sendable {
    let product: Product
    /// Cheapest first — and with the verdict on every row, so "cheapest" and
    /// "allowed" can visibly be different rows. They usually are.
    let alternatives: [Product]
    /// Whether these are other *shops* (the mock) or other *variants* of the
    /// same thing (live Instamart, which is one shop).
    let comparable: Bool
}
