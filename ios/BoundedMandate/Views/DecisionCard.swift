import SwiftUI

/// Money renders as a card inside the thread, never as a separate screen — the
/// card is punctuation at the end of a turn.
///
/// Every verdict uses this one view. A receipt and a refusal are the same
/// object wearing different colours, which is the honest shape: the engine ran
/// the same checks either way, and the reader should be able to parse both the
/// same way.
struct DecisionCard: View {
    @Environment(\.theme) private var theme
    let decision: Decision

    /// The cart opens by itself when something in it is the reason for the
    /// verdict — that is the moment the reader needs to see the lines, and
    /// making them tap for it would withhold the answer to the question the
    /// card just raised.
    @State private var showingCart: Bool?
    private var cartOpen: Bool { showingCart ?? !decision.flagged.isEmpty }

    private var tint: Color { theme.color(for: decision.verdict) }

    var body: some View {
        Card(tint: tint) {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 7) {
                        Image(systemName: decision.verdict.symbol)
                            .font(.system(size: 13))
                            .foregroundStyle(tint)
                        Eyebrow(text: decision.verdict.headline, color: tint)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(rupees(decision.realTotalPaise))
                            .font(.system(size: 34, weight: .bold))
                            .monospacedDigit()
                            .kerning(-0.8)
                            .foregroundStyle(theme.textNormal)
                            .textSelection(.enabled)

                        if decision.lied {
                            Text("the agent reported \(rupees(decision.claimedTotalPaise))")
                                .font(.system(size: 13))
                                .foregroundStyle(theme.negative)
                        } else if let merchant = decision.merchant {
                            Text(merchant)
                                .font(.system(size: 13))
                                .foregroundStyle(theme.textMuted)
                        }
                    }

                    if !decision.reasons.isEmpty {
                        VStack(alignment: .leading, spacing: 13) {
                            ForEach(decision.reasons, id: \.self) { reason in
                                ReasonRow(reason: reason, tint: tint)
                            }
                        }
                    }
                }
                .padding(18)

                if !decision.items.isEmpty {
                    Divider().overlay(theme.borderSubtle)
                    cart
                }

                Divider().overlay(theme.borderSubtle)

                VStack(spacing: 9) {
                    // Words, not codes. The reference is worth showing because
                    // it is something the user could quote back to support; the
                    // reason string is not.
                    DetailRow(label: "Outcome", value: decision.settlement, color: tint)
                    if let reference = decision.paymentID ?? decision.orderID {
                        DetailRow(label: "Reference", value: reference, mono: true)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
            }
        }
    }

    @ViewBuilder private var cart: some View {
        Button {
            withAnimation(.snappy(duration: 0.28)) { showingCart = !cartOpen }
        } label: {
            HStack(spacing: 6) {
                Text(
                    decision.flagged.isEmpty
                        ? plural(decision.items.count, "item")
                        : "\(decision.flagged.count) of \(plural(decision.items.count, "item")) flagged"
                )
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(decision.flagged.isEmpty ? theme.textMuted : tint)
                Image(systemName: "chevron.down")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(theme.textMuted)
                    .rotationEffect(.degrees(cartOpen ? 180 : 0))
                Spacer()
            }
            .padding(.horizontal, 18)
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)

        if cartOpen {
            VStack(spacing: 0) {
                ForEach(decision.orderedItems) { line in
                    HStack(spacing: 10) {
                        Text(line.name)
                            .font(.system(size: 14))
                            .foregroundStyle(line.flagged ? theme.textNormal : theme.textSubtle)
                            .lineLimit(2)
                        if let note = line.note {
                            Text(note)
                                .font(.system(size: 11, weight: .medium))
                                .foregroundStyle(tint)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(tint.opacity(0.14), in: .capsule)
                        }
                        Spacer(minLength: 8)
                        Text(rupees(line.pricePaise))
                            .font(.system(size: 14))
                            .monospacedDigit()
                            .foregroundStyle(line.flagged ? theme.textNormal : theme.textMuted)
                    }
                    .padding(.horizontal, 18)
                    .padding(.vertical, 6)
                }
            }
            .padding(.bottom, 8)
        }
    }
}
