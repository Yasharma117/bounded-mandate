import SwiftUI

/// Every card in the app is one of these, so the glass treatment is decided
/// once rather than per component.
///
/// `.glassEffect` is first-party here: the tint, the interactivity and the
/// shape all come from one modifier, and the system handles refraction against
/// whatever is behind it.
struct Card<Content: View>: View {
    @Environment(\.theme) private var theme
    var tint: Color?
    @ViewBuilder var content: Content

    var body: some View {
        content
            .glassEffect(
                tint.map { .regular.tint($0.opacity(0.10)) } ?? .regular,
                in: .rect(cornerRadius: 22)
            )
    }
}

/// The small uppercase label that opens every card.
struct Eyebrow: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 11, weight: .bold))
            .kerning(0.8)
            .foregroundStyle(color)
    }
}

/// A label/value line. The value wraps rather than colliding with its label —
/// `reason_code` reaches 74 characters on the compromised-agent run.
struct DetailRow: View {
    @Environment(\.theme) private var theme
    let label: String
    let value: String
    var mono = false
    var color: Color?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(label)
                .font(.system(size: 13))
                .foregroundStyle(theme.textMuted)
                .layoutPriority(1)
            Spacer(minLength: 0)
            Text(value)
                .font(.system(size: 13, design: mono ? .monospaced : .default))
                .monospacedDigit()
                .multilineTextAlignment(.trailing)
                .foregroundStyle(color ?? theme.textNormal)
                .textSelection(.enabled)
        }
    }
}

/// One reason, in words. The machine code stays in the ledger where it belongs;
/// a card is not the place to teach someone `category.not_allowed`.
struct ReasonRow: View {
    @Environment(\.theme) private var theme
    let reason: Reason
    let tint: Color

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Capsule().fill(tint).frame(width: 2)
            VStack(alignment: .leading, spacing: 3) {
                Text(reason.title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(tint)
                Text(reason.detail)
                    .font(.system(size: 14))
                    .foregroundStyle(theme.textNormal)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
