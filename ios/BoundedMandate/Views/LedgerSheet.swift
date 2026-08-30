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
    @State private var stats: Stats?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let page {
                        chain(page)
                        if let stats { tally(stats) }
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
            do {
                // Both read the same entries. Fetched together so the tally and
                // the rows below it can never disagree about what happened.
                page = try await Engine.readLedger()
                stats = try await Engine.readStats()
            } catch {
                problem = error.localizedDescription
            }
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

    /// What the trail adds up to.
    ///
    /// The rows below say what happened one at a time; a reader who has thirty
    /// seconds needs the total. `held back` is the figure worth leading with —
    /// authorised money is what any checkout produces, and money an autonomous
    /// agent asked for and did not get is what only this does.
    private func tally(_ stats: Stats) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 0) {
                    figure(rupees(stats.authorisedPaise), "authorised", theme.textNormal)
                    Divider().frame(height: 34).overlay(theme.borderSubtle)
                    figure(rupees(stats.heldBackPaise), "held back", theme.notice)
                }
                .padding(.vertical, 16)

                Divider().overlay(theme.borderSubtle)

                HStack(spacing: 6) {
                    Text("\(stats.decisions) decisions")
                    dot
                    Text("\(stats.allowed) allowed")
                    dot
                    Text("\(stats.refused) refused")
                    if stats.settlements > 0 {
                        dot
                        Text(plural(stats.settlements, "order", "orders") + " paid")
                    }
                    Spacer(minLength: 0)
                }
                .font(.system(size: 12))
                .foregroundStyle(theme.textMuted)
                .padding(.horizontal, 16)
                .padding(.vertical, 11)

                if !stats.reasons.isEmpty {
                    Divider().overlay(theme.borderSubtle)
                    VStack(alignment: .leading, spacing: 7) {
                        Eyebrow(text: "What the rule stopped", color: theme.textMuted)
                        ForEach(stats.reasons, id: \.label) { reason in
                            HStack(spacing: 8) {
                                Text("\(reason.count)")
                                    .font(.system(size: 13, weight: .semibold))
                                    .monospacedDigit()
                                    .foregroundStyle(theme.notice)
                                    .frame(minWidth: 16, alignment: .trailing)
                                Text(reason.label)
                                    .font(.system(size: 13))
                                    .foregroundStyle(theme.textSubtle)
                                    .fixedSize(horizontal: false, vertical: true)
                                Spacer(minLength: 0)
                            }
                        }
                    }
                    .padding(16)
                }
            }
        }
    }

    private var dot: some View {
        Text("·").foregroundStyle(theme.textMuted.opacity(0.6))
    }

    private func figure(_ value: String, _ label: String, _ tint: Color) -> some View {
        VStack(spacing: 3) {
            Text(value)
                .font(.system(size: 22, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(theme.textMuted)
        }
        .frame(maxWidth: .infinity)
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
