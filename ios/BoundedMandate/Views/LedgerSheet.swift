import SwiftUI

/// The audit trail, and whether it still verifies.
///
/// The chain is the product's one checkable claim — every entry carries the
/// SHA-256 of the entry before it, so "append-only" is something you can test
/// rather than something you are asked to believe. That check belongs on screen,
/// not only in a test file.
///
/// Reached from the Ledger verb, from "See what it tried" on a refusal, and from
/// "Verify the chain" on a receipt. All three used to open the chat thread.
struct LedgerSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    @State private var page: LedgerPage?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let page {
                        chain(page)
                        Card {
                            VStack(spacing: 0) {
                                ForEach(Array(page.entries.reversed().enumerated()), id: \.element.id) {
                                    index, entry in
                                    if index > 0 {
                                        Divider().overlay(theme.borderSubtle.opacity(0.5))
                                            .padding(.leading, 16)
                                    }
                                    row(entry)
                                }
                            }
                        }
                        Text(
                            "Every entry carries the hash of the one before it. Editing, "
                            + "reordering or removing any of them breaks the chain from that "
                            + "point on — which is what the line above checks."
                        )
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textMuted)
                        .padding(.horizontal, 4)
                    } else if problem == nil {
                        ProgressView().tint(theme.primary)
                            .frame(maxWidth: .infinity).padding(.top, 60)
                    }
                    if let problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                    }
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("Audit ledger")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .task {
            do { page = try await Engine.readLedger() } catch { problem = error.localizedDescription }
        }
    }

    private func chain(_ page: LedgerPage) -> some View {
        Card(tint: page.chainIntact ? nil : theme.negative) {
            HStack(spacing: 10) {
                Image(systemName: page.chainIntact ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(page.chainIntact ? theme.primary : theme.negative)
                VStack(alignment: .leading, spacing: 2) {
                    Text(page.chainIntact ? "The chain verifies" : "The chain is broken")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(theme.textNormal)
                    Text("\(plural(page.entries.count, "entry", "entries")) checked just now")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textMuted)
                }
                Spacer(minLength: 0)
            }
            .padding(16)
        }
    }

    private func row(_ entry: LedgerEntry) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(entry.seq)")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(theme.textMuted)
                .frame(width: 22, alignment: .trailing)
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.headline)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(entry.verdict.map { theme.color(for: $0) } ?? theme.textNormal)
                // The machine name, deliberately. This is the one screen where
                // the code is the point — it is what a person could quote back.
                if let code = entry.reasonCode {
                    Text(code)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(theme.textMuted)
                        .textSelection(.enabled)
                }
                if let payment = entry.paymentID {
                    Text(payment)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(theme.textMuted)
                        .textSelection(.enabled)
                }
            }
            Spacer(minLength: 8)
            if let paise = entry.totalPaise {
                Text(rupees(paise))
                    .font(.system(size: 13))
                    .monospacedDigit()
                    .foregroundStyle(theme.textSubtle)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
    }
}
