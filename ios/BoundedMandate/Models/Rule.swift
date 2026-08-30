import Foundation

/// The standing rule as the engine actually holds it, and the options the
/// controls may offer.
///
/// Served rather than assembled on the client, so what an edit screen shows is
/// what the engine enforces — a control describing a policy that is not the one
/// being applied would be worse than no screen at all.
struct RuleBounds: Decodable, Hashable, Sendable {
    let perTxnMaxPaise: Int
    let merchants: [String]
    let categories: [String]
    let everyDays: Int
    let ordersPerWindow: Int
    let deliveryAddresses: [String]
    /// A cap above this is a typo, not a rule. Sent so the field can refuse it
    /// at the keyboard rather than at the server.
    let maxCapPaise: Int
    let merchantOptions: [String]
    let categoryOptions: [String]

    enum CodingKeys: String, CodingKey {
        case merchants, categories
        case perTxnMaxPaise = "per_txn_max_paise"
        case everyDays = "every_days"
        case ordersPerWindow = "orders_per_window"
        case deliveryAddresses = "delivery_addresses"
        case maxCapPaise = "max_cap_paise"
        case merchantOptions = "merchant_options"
        case categoryOptions = "category_options"
    }
}

/// Rupees-and-paise text to paise, or `nil` if it is not a number.
///
/// Parsed as text rather than through `Double`, because `2000.10` is not
/// exactly representable in binary floating point and a cap that lands a paisa
/// off is a bound the user did not set. Integers all the way down, the same
/// rule the engine follows.
func paise(from typed: String) -> Int? {
    let text = typed
        .replacingOccurrences(of: ",", with: "")
        .replacingOccurrences(of: "₹", with: "")
        .trimmingCharacters(in: .whitespaces)
    guard !text.isEmpty else { return nil }

    let parts = text.split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count <= 2, let rupees = Int(parts[0].isEmpty ? "0" : String(parts[0])), rupees >= 0
    else { return nil }
    if parts.count == 1 { return rupees * 100 }

    // "2000.5" is fifty paise, not five — pad rather than parse as written.
    let fraction = String(parts[1].prefix(2)).padding(toLength: 2, withPad: "0", startingAt: 0)
    guard parts[1].count <= 2, let sub = Int(fraction) else { return nil }
    return rupees * 100 + sub
}

/// Paise back to the text a field opens on. Always two decimal places, because
/// the point of the field is that the user sees the exact figure they are setting.
func typedAmount(_ paise: Int) -> String {
    String(format: "%d.%02d", paise / 100, paise % 100)
}
