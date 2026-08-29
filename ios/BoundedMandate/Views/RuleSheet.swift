import SwiftUI

/// The standing rule, in full.
///
/// The central object of the whole product, and until now it had no screen —
/// `MandateCard` existed but only a one-time grant ever reached it. "View rule"
/// opened the chat thread.
struct RuleSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    let rule: Rule
    let lists: [ShoppingList]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    MandateCard(bounds: rule.bounds, title: "Your standing rule")

                    if let delivery = rule.delivery {
                        Card {
                            VStack(alignment: .leading, spacing: 10) {
                                Eyebrow(text: "Delivering to", color: theme.textMuted)
                                Text(delivery.label)
                                    .font(.system(size: 16, weight: .semibold))
                                    .foregroundStyle(theme.textNormal)
                                Text(delivery.line)
                                    .font(.system(size: 13))
                                    .foregroundStyle(theme.textMuted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(16)
                        }
                    }

                    if !lists.isEmpty {
                        Eyebrow(text: "What it runs", color: theme.textMuted)
                        ForEach(lists) { list in
                            ShoppingListCard(list: list, editable: false)
                        }
                    }

                    Text(
                        "Nothing here widens on its own. The agent cannot read this rule, "
                        + "cannot change it, and cannot approve itself against it — every "
                        + "basket it proposes is checked against these bounds by the engine, "
                        + "which fetches the basket itself rather than taking the agent's word."
                    )
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textMuted)
                    .padding(.horizontal, 4)
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("Your rule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
