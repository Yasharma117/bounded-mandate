import SwiftUI

/// The spine is a conversation. A user turn is a capsule on the right; the
/// agent speaks plainly on the left, wider, because it is the one explaining
/// itself.
struct Bubble: View {
    @Environment(\.theme) private var theme
    let from: Author
    let text: String

    enum Author { case user, agent }

    var body: some View {
        HStack {
            if from == .user { Spacer(minLength: 40) }

            Text(text)
                .font(.system(size: 16))
                .foregroundStyle(from == .user ? theme.textNormal : theme.textSubtle)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, from == .user ? 15 : 2)
                .padding(.vertical, from == .user ? 11 : 2)
                .glassEffect(from == .user ? .regular : .identity, in: .rect(cornerRadius: 20))

            if from == .agent { Spacer(minLength: 40) }
        }
    }
}
