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

                Divider().overlay(theme.borderSubtle)

                VStack(spacing: 9) {
                    DetailRow(
                        label: "Decision",
                        value: decision.reasonCode,
                        mono: true,
                        color: tint
                    )
                    if let payment = decision.paymentID {
                        DetailRow(label: "Paid", value: payment, mono: true)
                    } else if let order = decision.orderID {
                        DetailRow(label: "Order", value: order, mono: true)
                    } else {
                        DetailRow(label: "Reached the rail", value: "no")
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
            }
        }
    }
}
