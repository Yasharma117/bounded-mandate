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

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let home = store.home {
                        rule(home.rule)
                        pots(home.lists)
                        verbs
                        StateCard(
                            home: home,
                            onAction: { act in take(act, home) },
                            onDismiss: { Task { await store.dismiss() } }
                        )
                        .arrives(reduceMotion)
                        .id(home.state + home.headline)
                        recent(home)
                    } else if store.problem == nil {
                        ProgressView().tint(theme.primary)
                            .frame(maxWidth: .infinity).padding(.top, 60)
                    }

                    if let problem = store.problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                    }
                }
                .padding(16)
                .animation(Motion.respectful(Motion.move(), reduced: reduceMotion), value: store.home?.state)
            }
            .scrollEdgeEffectStyle(.hard, for: .top)
            .background(Backdrop())
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
                .font(.system(size: 36, weight: .medium))
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
                    VStack(alignment: .leading, spacing: 6) {
                        Eyebrow(text: list.name, color: theme.textMuted)
                        Text(rupees(list.totalPaise))
                            .font(.system(size: 22, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(theme.textNormal)
                        Text(list.schedule)
                            .font(.system(size: 12))
                            .foregroundStyle(theme.textMuted)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
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
            verb("checkmark.seal", "Ledger") { showingThread = true }
        }
    }

    private func verb(_ symbol: String, _ label: String, _ act: @escaping () -> Void) -> some View {
        Button(action: act) {
            VStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 20))
                    .foregroundStyle(theme.primary)
                    .frame(width: 64, height: 64)
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

    private func take(_ act: HomeAction, _ home: Home) {
        switch act.id {
        case "view_rule", "view_basket", "see_attempt", "verify_chain", "drop_flagged",
             "classify", "leave_out", "approve_once":
            // Everything that needs the cart, the reasons or a conversation
            // belongs in the thread, which already renders all three.
            showingThread = true
        case "pause", "resume":
            showingList = true
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

    private func runDue() async {
        try? await Engine.runDue()
        await store.load()
    }
}
