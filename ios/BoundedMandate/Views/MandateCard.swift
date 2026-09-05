import SwiftUI

/// The standing rule, as bounds rather than prose.
///
/// The user said one sentence; the engine enforces five separate constraints.
/// Showing them as rows is the only way the difference between "under ₹2,000"
/// and "under ₹2,000, at Instamart, groceries only, to your home, once every
/// four days" becomes visible — and every one of those is a thing an agent has
/// been caught trying to cross.
struct MandateCard: View {
    @Environment(\.theme) private var theme
    let bounds: MandateBounds
    /// A one-time grant is the same object with a life measured in minutes.
    var expiresIn: String?
    /// Set for a grant, which is bound to one basket by `Policy.cart_id`.
    ///
    /// The card listed a cap, a shop and a scope and said nothing about the
    /// bound that matters most — so the person approving was not told the one
    /// thing that makes a grant safe to hand out. Without it, "approving this
    /// basket is not approving one like it" is a claim the screen never makes.
    var basketBound = false
    var title = "Your standing rule"

    /// A grant that expires is used once, not "once every 1 days".
    private var cadence: String {
        if expiresIn != nil { return "once, then it is gone" }
        let window = bounds.everyDays == 1 ? "day" : "\(bounds.everyDays) days"
        return bounds.ordersPerWindow == 1
            ? "once every \(window)"
            : "\(bounds.ordersPerWindow)× every \(window)"
    }

    var body: some View {
        Card(tint: expiresIn == nil ? nil : theme.orchid) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 7) {
                    Image(systemName: expiresIn == nil ? "checkmark.shield.fill" : "clock.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(expiresIn == nil ? theme.primary : theme.orchid)
                    Eyebrow(
                        text: title,
                        color: expiresIn == nil ? theme.primary : theme.orchid
                    )
                }
                .padding(18)

                Divider().overlay(theme.borderSubtle)

                VStack(spacing: 10) {
                    DetailRow(label: "Up to", value: "\(rupees(bounds.perTxnMaxPaise)) per order")
                    DetailRow(label: "At", value: bounds.merchants.joined(separator: ", "))
                    DetailRow(label: "Buying", value: bounds.categories.joined(separator: ", "))
                    DetailRow(label: "How often", value: cadence)
                    if basketBound {
                        DetailRow(label: "Only", value: "this basket, not one like it")
                    }
                    if let expiresIn {
                        DetailRow(label: "Expires", value: expiresIn, color: theme.orchid)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
            }
        }
    }
}
