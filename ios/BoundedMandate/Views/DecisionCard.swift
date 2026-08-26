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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL
    let decision: Decision

    /// What the user approved, once they have. Held here rather than in the
    /// thread because a grant belongs to the refusal that prompted it — it is
    /// the answer to this card, and reads wrong anywhere else.
    @State private var granted: GrantResponse?
    @State private var granting = false
    @State private var refusal: String?

    /// The cart opens by itself when something in it is the reason for the
    /// verdict — that is the moment the reader needs to see the lines, and
    /// making them tap for it would withhold the answer to the question the
    /// card just raised.
    @State private var showingCart: Bool?
    private var cartOpen: Bool { showingCart ?? !decision.flagged.isEmpty }

    private var tint: Color { theme.color(for: decision.verdict) }

    var body: some View {
        VStack(spacing: 12) {
            verdictCard
            if let granted {
                MandateCard(
                    bounds: granted.grant.bounds,
                    expiresIn: granted.grant.expiresIn,
                    title: "One-time approval"
                )
                .arrives(reduceMotion)
                if let checkout { reopen(checkout) }
            }
        }
    }

    private var checkout: URL? {
        granted?.payPath.flatMap(Engine.url(forPath:))
    }

    private var verdictCard: some View {
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

                if decision.grantable && granted == nil {
                    Divider().overlay(theme.borderSubtle)
                    approve
                }
            }
        }
    }

    /// The way out of a refusal that is *not* raising the rule.
    ///
    /// The button sends a cart id and nothing else. Everything it comes back
    /// with — the cap, the shop, the address, the fifteen minutes — was derived
    /// server-side from the basket the engine fetched, so what this affords is
    /// the choice to approve, never the terms of it.
    @ViewBuilder private var approve: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                Task { await mintGrant() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: granting ? "hourglass" : "checkmark.shield")
                        .font(.system(size: 13, weight: .semibold))
                    Text(granting ? "Approving…" : "Approve just this basket")
                        .font(.system(size: 14, weight: .semibold))
                    Spacer(minLength: 8)
                    Text(rupees(decision.realTotalPaise))
                        .font(.system(size: 14, weight: .semibold))
                        .monospacedDigit()
                }
                .foregroundStyle(theme.orchid)
                .padding(.horizontal, 18)
                .frame(minHeight: 48)
                .contentShape(.rect)
            }
            .buttonStyle(.pressable)
            .disabled(granting)

            if let refusal {
                Text(refusal)
                    .font(.system(size: 13))
                    .foregroundStyle(theme.negative)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 18)
                    .padding(.bottom, 12)
            }
        }
    }

    private func reopen(_ url: URL) -> some View {
        Link(destination: url) {
            HStack(spacing: 7) {
                Image(systemName: "arrow.up.right.square")
                Text("Open the checkout")
            }
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(theme.orchid)
            .frame(maxWidth: .infinity, minHeight: 48)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
    }

    private func mintGrant() async {
        granting = true
        refusal = nil
        defer { granting = false }
        do {
            let response = try await Engine.grantOneTime(cartID: decision.cartID)
            withAnimation(Motion.respectful(Motion.enter(), reduced: reduceMotion)) {
                granted = response
            }
            // The user just approved it; making them tap a second time to reach
            // the checkout is friction with nothing behind it. The card stays
            // as the record of what was approved.
            if let checkout { openURL(checkout) }
        } catch {
            refusal = error.localizedDescription
        }
    }

    @ViewBuilder private var cart: some View {
        Button {
            withAnimation(Motion.respectful(Motion.move(), reduced: reduceMotion)) { showingCart = !cartOpen }
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
        .buttonStyle(.pressable)

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
