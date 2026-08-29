import SwiftUI

/// A shopping list the agent wrote out, waiting for you to say yes.
///
/// The agent has no tool that can create a list, and that is deliberate: one
/// that could redefine "my usual groceries" could then order the new definition
/// entirely within policy — an escalation that never trips a bound. So it drafts
/// and you confirm, which is the same arrangement the money side already has.
///
/// Before this it simply refused — "I don't have a tool to modify your shopping
/// list, you would need to add these items yourself" — which is true and useless.
struct ListDraftCard: View {
    @Environment(\.theme) private var theme
    let draft: ListDraft
    var onAdd: (ListDraft) -> Void

    @State private var added = false

    private var missing: Int { draft.items.count - draft.stockedCount }

    var body: some View {
        Card(tint: theme.orchid) {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 7) {
                        Image(systemName: "square.and.pencil")
                            .font(.system(size: 13))
                            .foregroundStyle(theme.orchid)
                        Eyebrow(text: added ? "Added to your lists" : "A list, for you to approve",
                                color: theme.orchid)
                    }
                    Text(draft.name)
                        .font(.system(size: 19, weight: .semibold))
                        .foregroundStyle(theme.textNormal)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("\(plural(draft.items.count, "item")) · \(draft.schedule)")
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textMuted)
                }
                .padding(18)

                Divider().overlay(theme.borderSubtle)

                VStack(spacing: 0) {
                    ForEach(Array(draft.items.enumerated()), id: \.element.id) { index, line in
                        if index > 0 {
                            Divider().overlay(theme.borderSubtle.opacity(0.5)).padding(.leading, 18)
                        }
                        row(line)
                    }
                }

                if missing > 0 {
                    Divider().overlay(theme.borderSubtle)
                    Text(
                        "\(plural(missing, "line")) the shop your rule allows does not stock. "
                        + "You can still keep the list — those lines will simply need you the "
                        + "first time it runs."
                    )
                    .font(.system(size: 12))
                    .foregroundStyle(theme.notice)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(18)
                }

                if !added {
                    Divider().overlay(theme.borderSubtle)
                    Button {
                        added = true
                        onAdd(draft)
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "checkmark")
                                .font(.system(size: 13, weight: .semibold))
                            Text("Add this list")
                                .font(.system(size: 15, weight: .semibold))
                            Spacer(minLength: 0)
                        }
                        .foregroundStyle(theme.orchid)
                        .padding(.horizontal, 18)
                        .frame(minHeight: 50)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.pressable)
                }
            }
        }
    }

    private func row(_ line: ListDraft.Line) -> some View {
        HStack(spacing: 12) {
            ProductThumb(url: line.imageURL, side: 34)
            Text(line.name)
                .font(.system(size: 14))
                .foregroundStyle(line.stocked ? theme.textNormal : theme.textMuted)
                .lineLimit(2)
            Spacer(minLength: 8)
            if let paise = line.pricePaise {
                Text(rupees(paise))
                    .font(.system(size: 14))
                    .monospacedDigit()
                    .foregroundStyle(theme.textSubtle)
            } else {
                Text("not stocked")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(theme.notice)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 9)
    }
}
