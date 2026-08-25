import SwiftUI

/// The list, always one tap away.
///
/// A source of truth has to be somewhere you can *go*, not something you scroll
/// back to find in a conversation. Every edit writes through to the engine and
/// comes back repriced, so what is on screen is what the agent will read — there
/// is no local copy that can drift from it.
@MainActor @Observable
final class ListStore {
    private(set) var list: ShoppingList?
    private(set) var problem: String?
    private(set) var saving = false

    func load() async {
        do {
            list = try await Engine.readList()
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    /// Optimistic, then reconciled: the row disappears immediately, and the
    /// server's repriced answer replaces it. If the write fails the server copy
    /// is restored, because the list is the one thing that must not silently
    /// diverge from what the agent reads.
    func remove(_ item: ListItem) async {
        guard let current = list else { return }
        let kept = current.items.filter { $0.name != item.name }
        await write(kept.map(\.name))
    }

    func add(_ name: String) async {
        guard let current = list, !current.items.contains(where: { $0.name == name }) else { return }
        await write(current.items.map(\.name) + [name])
    }

    private func write(_ names: [String]) async {
        saving = true
        defer { saving = false }
        do {
            list = try await Engine.writeList(items: names)
            problem = nil
        } catch {
            problem = error.localizedDescription
            await load()
        }
    }
}

struct ListSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    @State private var store = ListStore()
    @State private var adding = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let list = store.list {
                        ShoppingListCard(
                            list: list,
                            onRemove: { item in Task { await store.remove(item) } },
                            onAdd: { adding = true }
                        )
                        Text(
                            "The agent reads this list. It has no way to change it — "
                          + "only you do."
                        )
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textMuted)
                        .padding(.horizontal, 4)
                    } else if store.problem == nil {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                    }

                    if let problem = store.problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                            .padding(.horizontal, 4)
                    }
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("Shopping list")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .sheet(isPresented: $adding) {
                AddItemSheet { name in Task { await store.add(name) } }
            }
        }
        .task { await store.load() }
    }
}

/// Adding an item searches every shop, so the price the user is committing to
/// is visible before they commit — and so is the fact that the cheap one is
/// somewhere their rule does not reach.
struct AddItemSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    let onPick: (String) -> Void

    @State private var query = ""
    @State private var offers: [Offer] = []
    @State private var searching = false

    /// One row per product, priced at the shop the rule allows where there is
    /// one — adding to the list is choosing a product, not choosing a shop.
    private var products: [Offer] {
        Dictionary(grouping: offers, by: \.name)
            .values
            .compactMap { group in
                group.first(where: \.buyable) ?? group.min { $0.pricePaise < $1.pricePaise }
            }
            .sorted { $0.name < $1.name }
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(products) { offer in
                    Button {
                        onPick(offer.name)
                        dismiss()
                    } label: {
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(offer.name)
                                    .foregroundStyle(theme.textNormal)
                                    .lineLimit(2)
                                Text(offer.blockedReason ?? offer.merchant)
                                    .font(.system(size: 12))
                                    .foregroundStyle(
                                        offer.buyable ? theme.textMuted : theme.notice
                                    )
                            }
                            Spacer(minLength: 8)
                            Text(rupees(offer.pricePaise))
                                .monospacedDigit()
                                .foregroundStyle(theme.textSubtle)
                        }
                    }
                }
            }
            .overlay {
                if products.isEmpty, !query.isEmpty, !searching {
                    ContentUnavailableView.search(text: query)
                }
            }
            .searchable(text: $query, prompt: "Search every shop")
            .navigationTitle("Add an item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task(id: query) {
                searching = true
                defer { searching = false }
                offers = (try? await Engine.catalog(query)) ?? []
            }
        }
    }
}
