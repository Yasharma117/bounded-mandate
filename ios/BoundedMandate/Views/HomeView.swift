import SwiftUI

@MainActor @Observable
final class HomeStore {
    private(set) var home: Home?
    private(set) var problem: String?
    private(set) var working = false

    func load() async {
        do {
            home = try await Engine.readHome()
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    func dismiss() async {
        guard let key = home?.decision?.idempotencyKey else { return }
        working = true
        defer { working = false }
        try? await Engine.markSeen(key)
        await load()
    }
}

/// Where do I stand — answered before anybody asks.
///
/// The app's home used to be the thread, which told you nothing until you spoke
/// to it. That is the wrong metaphor for a product whose whole claim is that
/// **nobody is present**: a chat-first home says "drive me by talking", and the
/// thesis says "I ran while you were asleep."
///
/// It also gave an unattended decision nowhere to land. The scheduler proposes
/// at nine, the engine rules, the ledger records — and if you were not in the
/// thread at that moment, nothing ever told you. This screen is the answer to
/// that, and the state card is the part of it that matters.
struct HomeView: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL
    @State private var store = HomeStore()
    @State private var showingList = false
    @State private var showingAddress = false
    @State private var showingThread = false
    @State private var openVoice = false
    @State private var looking: CartLine?
    @State private var showingLedger = false
    @State private var showingRule = false
    @State private var showingBasket = false
    @State private var busy = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let home = store.home {
                        rule(home.rule)
                        pots(home.lists)
                        verbs
                        StateCard(
                            home: home,
                            onAction: { act in take(act, home) },
                            onDismiss: { Task { await store.dismiss() } },
                            onOpen: { line in looking = line }
                        )
                        .arrives(reduceMotion)
                        .id(home.state + home.headline)
                        recent(home)
                    } else if store.problem == nil {
                        ProgressView().tint(theme.primary)
                            .frame(maxWidth: .infinity).padding(.top, 60)
                    }

                    if let problem = store.problem {
                        disconnected(problem)
                    }
                }
                .padding(16)
                .animation(Motion.respectful(Motion.move(), reduced: reduceMotion), value: store.home?.state)
            }
            .scrollEdgeEffectStyle(.hard, for: .top)
            .background(backdrop)
            .navigationTitle("Bounded Mandate")
            // Inline, not large. The rule header directly below is the page's
            // real header, and a big title above it says the app's name twice
            // while spending a third of the screen to do it.
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingAddress = true } label: {
                        Image(systemName: "mappin.and.ellipse")
                    }
                    .accessibilityLabel("Delivery address")
                }
            }
            .sheet(isPresented: $showingList) { ListSheet() }
            // No swap offered from here: this is a basket that has already been
            // decided on, not a list the user is composing.
            .sheet(item: $looking) { line in
                ProductSheet(name: line.name, merchant: store.home?.rule.merchants.first ?? "instamart")
            }
            .sheet(isPresented: $showingLedger) { LedgerSheet() }
            .sheet(isPresented: $showingRule) {
                if let home = store.home { RuleSheet(rule: home.rule, lists: home.lists) }
            }
            .sheet(isPresented: $showingBasket) {
                if let decision = store.home?.decision {
                    BasketSheet(decision: decision) { line in looking = line }
                }
            }
            .sheet(isPresented: $showingAddress) { AddressSheet() }
            .fullScreenCover(isPresented: $showingThread) {
                ThreadView(startInVoice: openVoice)
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { commandBar }
            .task { await store.load() }
            // Coming back from the thread, a sheet or the checkout, the state
            // may have moved — an approval mints a grant, a payment spends it.
            .onChange(of: showingThread) { if !showingThread { Task { await store.load() } } }
            .onChange(of: showingList) { if !showingList { Task { await store.load() } } }
            .onChange(of: showingAddress) { if !showingAddress { Task { await store.load() } } }
        }
    }

    /// As a plain `.background` this is sized to the scroll view's *content*
    /// area, which is inset about 34pt on each side — so the page shows through
    /// down both edges as a lighter band the width of the content. Overshooting
    /// covers it; `ignoresSafeArea` alone does not, because the inset is not
    /// safe area. The same trap `ThreadView` documents, walked into again by
    /// reaching for `Backdrop()` bare.
    private var backdrop: some View {
        Backdrop()
            .padding(.horizontal, -80)
            .ignoresSafeArea()
    }

    /// The engine is not answering.
    ///
    /// Worth a real screen rather than a red line: this app holds no policy and
    /// no keys, so with the engine down there is nothing it can show and
    /// nothing it can do — and on a demo machine the likeliest cause is simply
    /// that the server is not running yet.
    private func disconnected(_ problem: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "bolt.horizontal.circle")
                .font(.system(size: 30))
                .foregroundStyle(theme.textMuted)
            Text("Can't reach the engine.")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(theme.textNormal)
            Text(Engine.baseURL.absoluteString)
                .font(.system(size: 13, design: .monospaced))
                .foregroundStyle(theme.textMuted)
                .textSelection(.enabled)
            Text(problem)
                .font(.system(size: 13))
                .foregroundStyle(theme.textMuted)
                .multilineTextAlignment(.center)
            Button("Try again") { Task { await store.load() } }
                .buttonStyle(.glass)
                .tint(theme.primary)
                .padding(.top, 4)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 60)
    }

    // MARK: - the rule, which finally has a screen

    private func rule(_ rule: Rule) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 7) {
                Image(systemName: "checkmark.shield.fill")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.primary)
                Eyebrow(text: "Your rule is running", color: theme.primary)
            }
            Text(rupees(rule.perTxnMaxPaise))
                .font(.system(size: 32, weight: .medium))
                .monospacedDigit()
                .kerning(-1.1)
                .foregroundStyle(theme.textNormal)
                .textSelection(.enabled)
            // One line, not four labelled rows. A rule is short enough to be a
            // sentence, and four rows would be four times the height for it.
            Text(rule.summary)
                .font(.system(size: 14))
                .foregroundStyle(theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 2)
    }

    // MARK: - the lists, as pots

    private func pots(_ lists: [ShoppingList]) -> some View {
        HStack(spacing: 12) {
            ForEach(lists.prefix(2)) { list in
                Card(tint: list.overCap ? theme.notice : nil) {
                    VStack(alignment: .leading, spacing: 7) {
                        Eyebrow(text: list.name, color: theme.textMuted)
                        Text(rupees(list.totalPaise))
                            .font(.system(size: 20, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(theme.textNormal)
                        // How much of the per-order cap this list would spend.
                        //
                        // It shipped unlabelled, which made it decoration —
                        // the first question anyone asked was what it measured.
                        // A meter that needs explaining is not doing its job,
                        // so the sentence under it says what the bar says.
                        GeometryReader { geo in
                            ZStack(alignment: .leading) {
                                Capsule().fill(theme.borderSubtle)
                                Capsule()
                                    .fill(list.overCap ? theme.notice : theme.primary)
                                    .frame(width: geo.size.width * min(1, list.capUsed))
                            }
                        }
                        .frame(height: 3)
                        Text(
                            list.overCap
                                ? "\(rupees(-list.headroomPaise)) over your cap"
                                : "\(rupees(list.headroomPaise)) under your cap"
                        )
                        .font(.system(size: 11))
                        .foregroundStyle(list.overCap ? theme.notice : theme.textMuted)
                        .lineLimit(1)
                        Text(list.schedule)
                            .font(.system(size: 11))
                            .foregroundStyle(theme.textMuted)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                }
            }
        }
        .onTapGesture { showingList = true }
    }

    // MARK: - four verbs

    private var verbs: some View {
        HStack(spacing: 0) {
            verb("play.fill", "Run now") { Task { await runDue() } }
            verb("list.bullet.rectangle", "Lists") { showingList = true }
            verb("mappin.and.ellipse", "Where") { showingAddress = true }
            verb("checkmark.seal", "Ledger") { showingLedger = true }
        }
    }

    private func verb(_ symbol: String, _ label: String, _ act: @escaping () -> Void) -> some View {
        Button(action: act) {
            VStack(spacing: 6) {
                Image(systemName: symbol)
                    .font(.system(size: 18))
                    .foregroundStyle(theme.primary)
                    .frame(width: 54, height: 54)
                    .background(theme.bgSubtle, in: .circle)
                Text(label.uppercased())
                    .font(.system(size: 11, weight: .medium))
                    .kerning(1.3)
                    .foregroundStyle(theme.textMuted)
            }
            .frame(maxWidth: .infinity)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
    }

    // MARK: - the audit trail, deliberately last

    private func recent(_ home: Home) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Eyebrow(text: "Recent", color: theme.textMuted)
                Spacer()
                Text(home.chainIntact ? "chain verifies" : "CHAIN BROKEN")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(home.chainIntact ? theme.textMuted : theme.negative)
            }
            ForEach(home.recent) { row in
                HStack(spacing: 10) {
                    Text(row.label)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(theme.textSubtle)
                    Text(row.summary)
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textMuted)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    if let paise = row.totalPaise {
                        Text(rupees(paise))
                            .font(.system(size: 13))
                            .monospacedDigit()
                            .foregroundStyle(theme.textMuted)
                    }
                }
            }
        }
        .padding(.horizontal, 2)
    }

    // MARK: - the command bar

    /// Not a composer — a way *into* one. Everything a conversation needs
    /// already lives in the thread, so this pushes there rather than growing a
    /// second copy of it that would drift.
    private var commandBar: some View {
        HStack(spacing: 10) {
            Button {
                openVoice = false
                showingThread = true
            } label: {
                HStack {
                    Text("Ask Bounded Mandate anything…")
                        .font(.system(size: 16))
                        .foregroundStyle(theme.textMuted)
                    Spacer()
                }
                .padding(.horizontal, 18)
                .frame(height: 52)
                .contentShape(.rect)
            }
            .buttonStyle(.pressable)
            .glassEffect(.regular, in: .capsule)

            Button {
                openVoice = true
                showingThread = true
            } label: {
                Image(systemName: "waveform")
                    .font(.system(size: 19))
                    .foregroundStyle(theme.primary)
                    .frame(width: 52, height: 52)
                    .contentShape(.rect)
            }
            .buttonStyle(.pressable)
            .glassEffect(.regular, in: .circle)
            .accessibilityLabel("Talk to it")
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
        .frame(maxWidth: .infinity)
        // The recent rows are meant to pass *under* this bar — least urgent
        // thing, pushed down. Without a scrim they are simply cut mid-line,
        // which reads as broken rather than as deprioritised.
        .background(alignment: .bottom) { scrim }
    }

    private var scrim: some View {
        LinearGradient(
            stops: [
                .init(color: theme.bgSubtle.opacity(0), location: 0),
                .init(color: theme.bgSubtle.opacity(0.72), location: 0.38),
                .init(color: theme.bgSubtle, location: 0.72),
                .init(color: theme.bgSubtle, location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(height: 200)
        .frame(maxWidth: .infinity)
        .padding(.bottom, -140)
        .allowsHitTesting(false)
    }

    // MARK: - acting on what the card offered

    /// Every offered action goes where it says it goes.
    ///
    /// All of these used to open the chat thread, on the reasoning that the
    /// thread already renders carts and reasons. It does — but "View rule" that
    /// lands you in a conversation is not a view of the rule, and a button that
    /// does not do the thing on it is worse than no button.
    private func take(_ act: HomeAction, _ home: Home) {
        switch act.id {
        case "view_rule":
            showingRule = true
        case "view_basket":
            // A decision has a basket of its own; before one exists, the thing
            // about to go out is a list.
            if home.decision != nil { showingBasket = true } else { showingList = true }
        case "see_attempt", "verify_chain":
            // Verifying *is* opening the ledger — the chain check is the first
            // thing on it, re-run on every read.
            showingLedger = true
        case "approve_once":
            Task { await approveOnce(home) }
        case "pause", "resume":
            Task { await setPaused(act.id == "pause", home) }
        case "classify":
            Task { await classifyFlagged(home) }
        case "leave_out", "drop_flagged":
            Task { await dropFlagged(home) }
        case "reauthorise", "cancel_basket":
            showingAddress = true
        case "not_now", "let_lapse":
            Task { await store.dismiss() }
        case "pay":
            if let id = home.grantID, let url = Engine.url(forPath: "/pay?grant=\(id)") {
                openURL(url)
            }
        default:
            showingThread = true
        }
    }

    // MARK: - actions that act

    /// Mint a one-time grant for this basket and open its checkout.
    ///
    /// The whole S3 flow, from a button that used to open a chat window.
    private func approveOnce(_ home: Home) async {
        guard let cartID = home.decision?.cartID, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let granted = try await Engine.grantOneTime(cartID: cartID)
            if let path = granted.payPath, let url = Engine.url(forPath: path) {
                openURL(url)
            }
        } catch {
            // A grant refused on the delivery address is recorded as a HALTED
            // event, so reloading surfaces the halt rather than swallowing it.
        }
        await store.load()
    }

    private func setPaused(_ paused: Bool, _ home: Home) async {
        guard let listID = home.listID ?? home.lists.first?.listID else {
            showingList = true
            return
        }
        _ = try? await Engine.setSchedule(listID, paused: paused)
        await store.load()
    }

    /// Which of the user's lists holds this line.
    ///
    /// Resolved here rather than guessed at server-side: a basket can come from
    /// a list run or from a one-off proposal, and editing "the list" when there
    /// isn't one would be an edit nobody asked for. No match means the list
    /// sheet opens instead of something silently changing.
    private func listHolding(_ name: String, _ home: Home) -> ShoppingList? {
        home.lists.first { $0.items.contains { $0.name == name } }
    }

    private func dropFlagged(_ home: Home) async {
        let flagged = (home.decision?.items ?? []).filter(\.flagged).map(\.name)
        guard !flagged.isEmpty, let list = flagged.compactMap({ listHolding($0, home) }).first
        else {
            showingList = true
            return
        }
        let kept = list.items.map(\.name).filter { !flagged.contains($0) }
        _ = try? await Engine.writeList(list.listID, items: kept)
        await store.dismiss()
    }

    private func classifyFlagged(_ home: Home) async {
        let unknown = (home.decision?.items ?? []).filter(\.unclassified).map(\.name)
        guard !unknown.isEmpty, let list = unknown.compactMap({ listHolding($0, home) }).first
        else {
            showingList = true
            return
        }
        // The user's own classification, which is the only one the engine will
        // take — it is why the list can be the source of what kind of thing a
        // line is, and why no model gets a say in it.
        var categories: [String: String] = [:]
        for item in list.items where !item.category.isEmpty { categories[item.name] = item.category }
        for name in unknown { categories[name] = "groceries" }
        _ = try? await Engine.writeList(
            list.listID, items: list.items.map(\.name), categories: categories
        )
        await store.dismiss()
    }

    private func runDue() async {
        try? await Engine.runDue()
        await store.load()
    }
}
