import SwiftUI

/// The one card on the home screen whose contents are the engine's current
/// state.
///
/// Every state uses this single view, the same way every verdict uses one
/// `DecisionCard`. A reassurance and an escalation are the same object wearing
/// different words, which is the honest shape: the engine ran the same checks
/// either way, and the reader should be able to parse both the same way.
///
/// **The actions are proposed and never taken.** That is not a UI convention
/// here, it is the engine's contract with the agent rendered as buttons — and
/// it is why a refusal arrives with no approval among them.
struct StateCard: View {
    @Environment(\.theme) private var theme
    let home: Home
    var onAction: (HomeAction) -> Void
    var onDismiss: () -> Void
    var onOpen: ((CartLine) -> Void)?

    private var tint: Color { home.tint(theme) }

    /// Every reason except the one already said as the headline sentence.
    private var others: [Reason] {
        (home.decision?.reasons ?? []).filter { $0.detail != home.detail }
    }

    var body: some View {
        Card(tint: tint) {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 7) {
                        Image(systemName: symbol)
                            .font(.system(size: 13))
                            .foregroundStyle(tint)
                        Eyebrow(text: home.chip, color: tint)
                        Spacer(minLength: 8)
                        if home.dismissable {
                            Button(action: onDismiss) {
                                Image(systemName: "xmark")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(theme.textMuted)
                                    .frame(width: 30, height: 30)
                                    .contentShape(.rect)
                            }
                            .buttonStyle(.pressable)
                            .accessibilityLabel("Dismiss")
                        }
                    }

                    VStack(alignment: .leading, spacing: 6) {
                        Text(home.headline)
                            .font(.system(size: 19, weight: .semibold))
                            .kerning(-0.2)
                            .foregroundStyle(theme.textNormal)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(home.detail)
                            .font(.system(size: 14))
                            .foregroundStyle(theme.textSubtle)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)

                        // The deciding reason is the sentence above. These are
                        // the others — a refusal usually has three, and running
                        // them together made one breath out of three separate
                        // problems.
                        if !others.isEmpty {
                            VStack(alignment: .leading, spacing: 5) {
                                ForEach(others.prefix(2), id: \.self) { reason in
                                    HStack(alignment: .top, spacing: 7) {
                                        Circle().fill(tint.opacity(0.7))
                                            .frame(width: 4, height: 4).padding(.top, 6)
                                        Text(reason.detail)
                                            .font(.system(size: 13))
                                            .foregroundStyle(theme.textMuted)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                                if others.count > 2 {
                                    Text("and \(others.count - 2) more")
                                        .font(.system(size: 12))
                                        .foregroundStyle(theme.textMuted)
                                        .padding(.leading, 11)
                                }
                            }
                            .padding(.top, 2)
                        }
                    }
                }
                .padding(16)

                if !home.strip.isEmpty {
                    Divider().overlay(theme.borderSubtle)
                    basket(home.strip)
                }

                if !home.actions.isEmpty {
                    Divider().overlay(theme.borderSubtle)
                    // Stacked, not a row. Three options side by side become
                    // three truncated words, and these are sentences a person
                    // is choosing between rather than buttons they are aiming at.
                    VStack(spacing: 0) {
                        ForEach(Array(home.actions.enumerated()), id: \.element.id) { i, act in
                            if i > 0 { Divider().overlay(theme.borderSubtle.opacity(0.5)) }
                            Button { onAction(act) } label: {
                                HStack(spacing: 8) {
                                    Text(act.label)
                                        .font(.system(size: 15, weight: i == 0 ? .semibold : .regular))
                                        .foregroundStyle(i == 0 ? tint : theme.textNormal)
                                    Spacer(minLength: 8)
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(theme.textMuted)
                                }
                                .padding(.horizontal, 18)
                                .frame(minHeight: 46)
                                .contentShape(.rect)
                            }
                            .buttonStyle(.pressable)
                        }
                    }
                }
            }
        }
    }

    /// What is actually in it, with pictures.
    ///
    /// Horizontal rather than a list: the card lives on a home screen among
    /// four other blocks, and twelve stacked rows would push everything below
    /// it off the page. A strip shows the first few, says how many more, and —
    /// with flagged lines sorted to the front — puts the two items that caused
    /// the interruption where the eye lands first.
    private func basket(_ goods: [CartLine]) -> some View {
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 12) {
                ForEach(goods) { line in
                    Button { onOpen?(line) } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        ZStack(alignment: .topTrailing) {
                            ProductThumb(url: line.imageURL, side: 52)
                            if line.flagged {
                                Image(systemName: "exclamationmark.circle.fill")
                                    .font(.system(size: 13))
                                    .foregroundStyle(tint)
                                    .background(theme.bgSubtle, in: .circle)
                                    .offset(x: 5, y: -5)
                            }
                        }
                        .overlay(
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .strokeBorder(line.flagged ? tint : .clear, lineWidth: 1.5)
                        )
                        Text(line.name)
                            .font(.system(size: 11))
                            .foregroundStyle(line.flagged ? theme.textNormal : theme.textMuted)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                        Text(rupees(line.pricePaise))
                            .font(.system(size: 11, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(line.flagged ? tint : theme.textSubtle)
                    }
                    .frame(width: 52)
                    .contentShape(.rect)
                    }
                    .buttonStyle(.pressable)
                    .disabled(onOpen == nil)
                }
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .scrollIndicators(.hidden)
    }

    private var symbol: String {
        switch home.state {
        case "at_rest": "checkmark.shield.fill"
        case "preflight": "clock.fill"
        case "ruled": "checkmark.seal.fill"
        case "paid": "shippingbox.fill"
        case "grant_live": "hourglass"
        default: home.decision?.verdict.symbol ?? "exclamationmark.triangle.fill"
        }
    }
}
