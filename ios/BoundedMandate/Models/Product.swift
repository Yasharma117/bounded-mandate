import Foundation

/// One buyable pack of a product.
///
/// Its own price, its own discount — and its own answer from the policy, which
/// is the thing Instamart's own sheet cannot tell you: `1 ltr` at ₹77 clears a
/// ₹2,000 rule and `1 ltr x 12` at ₹924 may not.
struct Pack: Decodable, Hashable, Sendable, Identifiable {
    let skuID: String
    /// The full name a list line would carry.
    let name: String
    /// Just the pack, for the tile — "1 ltr x 4".
    let label: String
    let pricePaise: Int
    /// Equal to `pricePaise` when there is no discount, so a struck-through
    /// figure is never shown where none exists.
    let mrpPaise: Int
    let off: Int
    /// The shop's own comparison, "7.7/100 ml" — the only honest way to weigh a
    /// 500ml against a six-pack.
    let unitPrice: String
    let inStock: Bool
    let withinCap: Bool

    var id: String { skuID }
    var discounted: Bool { off > 0 && mrpPaise > pricePaise }

    enum CodingKeys: String, CodingKey {
        case name, label, off
        case skuID = "sku_id"
        case pricePaise = "price_paise"
        case mrpPaise = "mrp_paise"
        case unitPrice = "unit_price"
        case inStock = "in_stock"
        case withinCap = "within_cap"
    }
}

/// One product as the shop describes it, packs and all.
struct Product: Decodable, Hashable, Sendable, Identifiable {
    let name: String
    let brand: String
    let merchant: String
    let imageURL: String?
    let category: String
    let rating: String
    let ratingCount: String
    /// "7 MINS", as the shop says it.
    let sla: String
    /// `nil` when the shop does not classify it.
    let veg: Bool?
    let badges: [String]
    let variants: [Pack]
    /// Two answers, never one — a shop can be allowed while what it sells is not.
    let merchantAllowed: Bool
    let categoryAllowed: Bool

    var id: String { merchant + name }
    var buyable: Bool { merchantAllowed && categoryAllowed }
    var cheapest: Pack? { variants.min { $0.pricePaise < $1.pricePaise } }

    var blockedReason: String? {
        switch (merchantAllowed, categoryAllowed) {
        case (true, true): nil
        case (false, true): "\(merchant) is not a shop your rule covers"
        case (true, false): "\(category.isEmpty ? "This" : category.capitalized) is not in your rule"
        case (false, false): "\(merchant) and \(category) are both outside your rule"
        }
    }

    enum CodingKeys: String, CodingKey {
        case name, brand, merchant, category, rating, sla, veg, badges, variants
        case imageURL = "image_url"
        case ratingCount = "rating_count"
        case merchantAllowed = "merchant_allowed"
        case categoryAllowed = "category_allowed"
    }
}

struct ProductDetail: Decodable, Sendable {
    let product: Product
    /// Other products, not other shops — except on the mock, where the honest
    /// answer is the same product at Blinkit and Zepto.
    let alternatives: [Product]
    let comparable: Bool
}
