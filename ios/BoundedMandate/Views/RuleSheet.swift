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
    /// Called once a rule is committed, so the home screen reloads from the
    /// engine rather than from what this sheet believes it sent.
    var onChanged: (() -> Void)?

    @State private var bounds: RuleBounds?
    @State private var editing = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    MandateCard(bounds: live ?? rule.bounds, title: "Your standing rule")

                    // The one screen where authority is created rather than
                    // spent, so the way in is a plain button rather than a
                    // gesture somebody could take by accident.
                    if let bounds {
                        Button { editing = true } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "slider.horizontal.3")
                                    .font(.system(size: 13, weight: .semibold))
                                Text("Change these bounds")
                                    .font(.system(size: 15, weight: .semibold))
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 12, weight: .semibold))
                            }
                            .foregroundStyle(theme.primary)
                            .padding(16)
                            .contentShape(.rect)
                        }
                        .buttonStyle(.pressable)
                        .glassEffect(.regular, in: .rect(cornerRadius: 22))
                        .sheet(isPresented: $editing) {
                            RuleEditor(bounds: bounds) { committed in
                                self.bounds = committed
                                onChanged?()
                            }
                        }
                    }

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
                        "Nothing here widens on its own. You set every bound above — "
                        + "speaking a rule fills them in, it does not set them, so a model "
                        + "mishearing you cannot become authority. The agent cannot read "
                        + "this rule, cannot change it, and cannot approve itself against "
                        + "it — every basket it proposes is checked against these bounds by "
                        + "the engine, which fetches the basket itself rather than taking "
                        + "the agent's word."
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
        .task {
            // The card can render from what the home screen already had; the
            // editor cannot, because it needs the options and the ceiling the
            // engine decides.
            bounds = try? await Engine.readRule()
        }
    }

    /// The bounds as last committed, falling back to what the home screen
    /// carried — so saving an edit updates this sheet without a round trip
    /// through the parent.
    private var live: MandateBounds? {
        bounds.map {
            MandateBounds(
                perTxnMaxPaise: $0.perTxnMaxPaise,
                merchants: $0.merchants,
                categories: $0.categories,
                everyDays: $0.everyDays,
                ordersPerWindow: $0.ordersPerWindow
            )
        }
    }
}
