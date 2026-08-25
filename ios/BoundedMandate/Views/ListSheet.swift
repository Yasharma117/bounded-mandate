import SwiftUI

/// The list, always one tap away.
///
/// A source of truth has to be somewhere you can *go*, not something you scroll
/// back to find in a conversation. Every edit writes through to the engine and
/// comes back repriced, so what is on screen is what the agent will read — there
/// is no local copy that can drift from it.
@MainActor @Observable
final class ListStore {
    private(set) var lists: [ShoppingList] = []
    private(set) var problem: String?
    private(set) var saving = false

    func load() async {
        do {
            lists = try await Engine.readLists()
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    /// Optimistic, then reconciled: the row goes immediately and the server's
    /// repriced answer replaces it. If the write fails the server copy is
    /// restored, because the list is the one thing that must not silently
    /// diverge from what the agent reads.
    func remove(_ item: ListItem, from list: ShoppingList) async {
        await write(list, items: list.items.filter { $0.name != item.name }.map(\.name))
    }

    func add(_ name: String, to list: ShoppingList) async {
        guard !list.items.contains(where: { $0.name == name }) else { return }
        await write(list, items: list.items.map(\.name) + [name])
    }

    func setPaused(_ paused: Bool, on list: ShoppingList) async {
        await replacing(list) { try await Engine.setSchedule(list.listID, paused: paused) }
    }

    func setCadence(_ days: Int, on list: ShoppingList) async {
        await replacing(list) { try await Engine.setSchedule(list.listID, everyDays: days) }
    }

    func create(name: String, items: [String], once: Bool, everyDays: Int?, runOn: String?)
        async
    {
        saving = true
        defer { saving = false }
        do {
            let made = try await Engine.createList(
                name: name, items: items, once: once, everyDays: everyDays, runOn: runOn
            )
            lists.append(made)
            await load()
        } catch {
            problem = error.localizedDescription
        }
    }

    func delete(_ list: ShoppingList) async {
        do {
            try await Engine.deleteList(list.listID)
            lists.removeAll { $0.listID == list.listID }
        } catch {
            problem = error.localizedDescription
        }
    }

    private func write(_ list: ShoppingList, items: [String]) async {
        await replacing(list) { try await Engine.writeList(list.listID, items: items) }
    }

    private func replacing(
        _ list: ShoppingList, _ work: @escaping () async throws -> ShoppingList
    ) async {
        saving = true
        defer { saving = false }
        do {
            let updated = try await work()
            if let index = lists.firstIndex(where: { $0.listID == list.listID }) {
                lists[index] = updated
            }
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
    @State private var addingTo: ShoppingList?
    @State private var creating = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if store.lists.isEmpty, store.problem == nil {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                    }

                    ForEach(store.lists) { list in
                        VStack(alignment: .leading, spacing: 8) {
                            ScheduleBar(list: list, store: store)
                            ShoppingListCard(
                                list: list,
                                onRemove: { item in
                                    Task { await store.remove(item, from: list) }
                                },
                                onAdd: { addingTo = list }
                            )
                        }
                    }

                    if let problem = store.problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                    }

                    Text(
                        "The agent reads these lists. It has no way to change them — "
                      + "only you do. A schedule says when it should try, never what "
                      + "it may buy."
                    )
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textMuted)
                    .padding(.horizontal, 4)
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("Shopping lists")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button { creating = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel("New list")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .sheet(item: $addingTo) { list in
                AddItemSheet { name in Task { await store.add(name, to: list) } }
            }
            .sheet(isPresented: $creating) {
                NewListSheet { name, once, everyDays, runOn in
                    Task {
                        await store.create(
                            name: name, items: [], once: once,
                            everyDays: everyDays, runOn: runOn
                        )
                    }
                }
            }
        }
        .task { await store.load() }
    }
}

/// When a list runs, and the controls for changing that. Above the card rather
/// than inside it, because *what* and *when* are two different decisions and
/// putting them in one box makes every edit look like the other kind.
private struct ScheduleBar: View {
    @Environment(\.theme) private var theme
    let list: ShoppingList
    let store: ListStore

    private var cadences: [Int] { [1, 2, 3, 4, 7, 14] }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: list.isOneOff ? "calendar" : "arrow.triangle.2.circlepath")
                .font(.system(size: 11, weight: .semibold))
            Text(list.schedule)
                .font(.system(size: 12, weight: .medium))
            if list.due, !list.paused, !list.spent {
                Text("due now")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(theme.primary)
            }

            Spacer()

            Menu {
                if !list.isOneOff {
                    Picker("Repeat", selection: cadenceBinding) {
                        ForEach(cadences, id: \.self) { days in
                            Text(days == 1 ? "Every day" : "Every \(days) days").tag(days)
                        }
                    }
                }
                Button(list.paused ? "Resume" : "Pause") {
                    Task { await store.setPaused(!list.paused, on: list) }
                }
                Button("Delete list", role: .destructive) {
                    Task { await store.delete(list) }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 13, weight: .semibold))
                    .frame(width: 44, height: 32)
                    .contentShape(.rect)
            }
        }
        .foregroundStyle(list.paused ? theme.textMuted : theme.textSubtle)
        .padding(.horizontal, 6)
    }

    private var cadenceBinding: Binding<Int> {
        Binding(
            get: { list.everyDays ?? 4 },
            set: { days in Task { await store.setCadence(days, on: list) } }
        )
    }
}

/// A new list. One-off lists ask for a date; repeating ones ask for a cadence.
private struct NewListSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    let onCreate: (String, Bool, Int?, String?) -> Void

    @State private var name = ""
    @State private var once = false
    @State private var everyDays = 4
    @State private var runOn = Date()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("What is this list for?", text: $name)
                }
                Section {
                    Picker("Kind", selection: $once) {
                        Text("Repeating").tag(false)
                        Text("One-off").tag(true)
                    }
                    .pickerStyle(.segmented)

                    if once {
                        DatePicker("On", selection: $runOn, displayedComponents: .date)
                    } else {
                        Stepper(
                            everyDays == 1 ? "Every day" : "Every \(everyDays) days",
                            value: $everyDays, in: 1...30
                        )
                    }
                } footer: {
                    Text(
                        "A schedule says when the agent should try. Your rule still "
                      + "decides what it may buy."
                    )
                }
            }
            .navigationTitle("New list")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        onCreate(
                            name, once,
                            once ? nil : everyDays,
                            once ? Self.iso.string(from: runOn) : nil
                        )
                        dismiss()
                    }
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }

    private static let iso: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
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
