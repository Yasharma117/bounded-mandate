import SwiftUI

/// The basket a decision was made about, line by line.
///
/// The home card shows a strip of it; this is all of it, including the fee lines
/// the strip filters out — because when the total is ₹159 on ₹116 of groceries,
/// the fees are the answer to "why is it that much".
struct BasketSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    let decision: LedgerDecision
    var onOpen: ((CartLine) -> Void)?

    private var tint: Color { theme.color(for: decision.verdict) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Card(tint: tint) {
                        VStack(spacing: 0) {
                            // Flagged first — the lines that caused the verdict
                            // should not be the ones you scroll to find.
                            ForEach(Array(decision.ordered.enumerated()), id: \.element.id) {
                                index, line in
                                if index > 0 {
                                    Divider().overlay(theme.borderSubtle.opacity(0.5))
                                        .padding(.leading, 16)
                                }
                                row(line)
                            }
                            Divider().overlay(theme.borderSubtle)
                            HStack {
                                Text("Total")
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundStyle(theme.textNormal)
                                Spacer()
                                Text(rupees(decision.totalPaise))
                                    .font(.system(size: 17, weight: .semibold))
                                    .monospacedDigit()
                                    .foregroundStyle(theme.textNormal)
                            }
                            .padding(16)
                        }
                    }
                    if !decision.reasons.isEmpty {
                        Eyebrow(text: "Why", color: tint)
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(decision.reasons, id: \.self) { reason in
                                ReasonRow(reason: reason, tint: tint)
                            }
                        }
                        .padding(.horizontal, 4)
                    }
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("The basket")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }

    private func row(_ line: CartLine) -> some View {
        Button { onOpen?(line) } label: {
            HStack(spacing: 12) {
                ProductThumb(url: line.imageURL, side: 40)
                VStack(alignment: .leading, spacing: 3) {
                    Text(line.name)
                        .font(.system(size: 14))
                        .foregroundStyle(line.flagged ? theme.textNormal : theme.textSubtle)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    if let note = line.note {
                        Text(note)
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(tint)
                    }
                }
                Spacer(minLength: 8)
                Text(rupees(line.pricePaise))
                    .font(.system(size: 14))
                    .monospacedDigit()
                    .foregroundStyle(line.flagged ? theme.textNormal : theme.textMuted)
            }
            .padding(.horizontal, 16)
            .frame(minHeight: 56)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
        .disabled(onOpen == nil || line.category == "fees")
    }
}
