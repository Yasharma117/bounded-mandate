import Foundation

/// A one-time approval: authority over **one basket**, for fifteen minutes,
/// spendable once.
///
/// It is not an exception to the standing rule and not a raised cap — it is a
/// second mandate, derived server-side from the cart the *engine* fetched. The
/// app cannot author one: it names a basket and is handed back bounds it had no
/// part in writing, which is the same asymmetry the agent lives under.
struct Grant: Decodable, Hashable, Sendable, Identifiable {
    let grantID: String
    let perTxnMaxPaise: Int
    let merchants: [String]
    let categories: [String]
    let deliveryAddress: String
    let everyDays: Int
    let ordersPerWindow: Int
    let cartID: String?
    /// ISO-8601 as the server wrote it. Kept as sent so the countdown is the
    /// server's clock rather than this device's idea of one.
    let expiresAt: String?
    /// `ready`, `paid`, `expired`, `stale`, or `refused`.
    let state: String

    //: The three below come only from `GET /api/grant/{id}`. Minting a grant
    //: answers with bounds alone — there is no payment to describe yet — so
    //: they are optional rather than defaulted, and a card that has not asked
    //: the engine can tell "not paid" from "not asked".
    let paymentID: String?
    let amountPaise: Int?
    let merchant: String?

    var id: String { grantID }

    /// Money moved, and the engine says so — not the checkout, and not a URL
    /// somebody opened.
    var paid: Bool { state == "paid" }

    enum CodingKeys: String, CodingKey {
        case grantID = "grant_id"
        case perTxnMaxPaise = "per_txn_max_paise"
        case merchants, categories, state, merchant
        case paymentID = "payment_id"
        case amountPaise = "amount_paise"
        case deliveryAddress = "delivery_address"
        case everyDays = "every_days"
        case ordersPerWindow = "orders_per_window"
        case cartID = "cart_id"
        case expiresAt = "expires_at"
    }

    /// The same object the standing-rule card renders, so a grant and a rule
    /// are read the same way — which is the point being made.
    var bounds: MandateBounds {
        MandateBounds(
            perTxnMaxPaise: perTxnMaxPaise,
            merchants: merchants,
            categories: categories,
            everyDays: everyDays,
            ordersPerWindow: ordersPerWindow
        )
    }

    /// "in 14 minutes", or nil if the server sent no expiry.
    var expiresIn: String? {
        guard let expiresAt, let when = isoDate(expiresAt) else { return nil }
        let left = when.timeIntervalSinceNow
        if left <= 0 { return "lapsed" }
        return "in \(plural(max(1, Int(left / 60)), "minute"))"
    }
}

/// What `POST /api/mandate/one-time` answers with: the bounds, the verdict the
/// engine reached under them, and the checkout — which is present only when
/// that verdict was an ALLOW.
struct GrantResponse: Decodable, Sendable {
    let grant: Grant
    let decision: Decision?
    let payPath: String?

    enum CodingKeys: String, CodingKey {
        case grant, decision
        case payPath = "pay_url"
    }
}

/// Python's `.isoformat()` emits fractional seconds only when there are any, and
/// `ISO8601DateFormatter` refuses whichever form it was not configured for —
/// returning nil, which reads on screen as "no expiry". That is the one thing a
/// grant must never look like, so both forms are tried.
func isoDate(_ text: String) -> Date? {
    let forms: [ISO8601DateFormatter.Options] = [
        [.withInternetDateTime, .withFractionalSeconds],
        [.withInternetDateTime],
    ]
    for options in forms {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = options
        if let date = formatter.date(from: text) { return date }
    }
    return nil
}
