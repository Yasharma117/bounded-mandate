import SwiftUI

/// One product, every shop, cheapest first.
///
/// The card exists to make one thing unmissable: the cheapest price is often at
/// a shop the mandate does not cover. Sorting by price and *then* marking which
/// rows are allowed puts that conflict in front of the reader rather than
/// hiding it by filtering the disallowed shops out — a filtered list would
/// quietly answer a different question than the one they asked.
struct OffersCard: View {
    @Environment(\.theme) private var theme
    @Environment(\.openURL) private var openURL

    let product: String
    let offers: [Offer]

    private var sorted: [Offer] { offers.sorted { $0.pricePaise < $1.pricePaise } }
    private var cheapest: Offer? { sorted.first }
    private var bestAllowed: Offer? { sorted.first(where: \.buyable) }

    /// What staying inside the mandate costs, when it costs anything.
    private var premiumPaise: Int? {
        guard let cheapest, let bestAllowed, bestAllowed.id != cheapest.id else { return nil }
        return bestAllowed.pricePaise - cheapest.pricePaise
    }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 6) {
                    Eyebrow(text: plural(offers.count, "shop"), color: theme.primary)
                    Text(product)
                        .font(.system(size: 19, weight: .semibold))
                        .foregroundStyle(theme.textNormal)
                        .fixedSize(horizontal: false, vertical: true)
                    if let premiumPaise {
                        Text("Staying on your list costs \(rupees(premiumPaise)) more")
                            .font(.system(size: 13))
                            .foregroundStyle(theme.notice)
                    }
                }
                .padding(18)

                Divider().overlay(theme.borderSubtle)

                ForEach(Array(sorted.enumerated()), id: \.element.id) { index, offer in
                    if index > 0 {
                        Divider().overlay(theme.borderSubtle.opacity(0.5)).padding(.leading, 18)
                    }
                    OfferRow(
                        offer: offer,
                        // A "cheapest" badge on the only row says nothing.
                        isCheapest: sorted.count > 1 && offer.id == cheapest?.id
                    ) {
                        if let url = URL(string: Engine.baseURL.absoluteString + offer.url) {
                            openURL(url)
                        }
                    }
                }
            }
        }
    }
}

private struct OfferRow: View {
    @Environment(\.theme) private var theme
    let offer: Offer
    let isCheapest: Bool
    let onOpen: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: offer.buyable ? "checkmark.circle.fill" : "slash.circle")
                .font(.system(size: 14))
                .foregroundStyle(offer.buyable ? theme.primary : theme.textMuted.opacity(0.7))

            VStack(alignment: .leading, spacing: 2) {
                Text(offer.merchant)
                    .font(.system(size: 15, weight: offer.buyable ? .medium : .regular))
                    .foregroundStyle(theme.textNormal)
                Text(offer.blockedReason ?? "in your rule")
                    .font(.system(size: 12))
                    .foregroundStyle(offer.buyable ? theme.textMuted : theme.notice)
            }

            Spacer(minLength: 8)

            VStack(alignment: .trailing, spacing: 2) {
                Text(rupees(offer.pricePaise))
                    .font(.system(size: 16, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(theme.textNormal)
                if isCheapest {
                    Text("cheapest")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(theme.textMuted)
                }
            }

            Button(action: onOpen) {
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(theme.textMuted)
                    .frame(width: 30, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.pressable)
        }
        .padding(.leading, 18)
        .padding(.trailing, 8)
        .padding(.vertical, 6)
        // The disallowed shops stay legible — dimmed enough to rank below the
        // allowed one, never so dim that the price becomes hard to read.
        .opacity(offer.buyable ? 1 : 0.72)
    }
}
