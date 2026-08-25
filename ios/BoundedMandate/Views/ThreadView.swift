import SwiftUI

/// Openers, not scenarios. Each is a thing a person would actually say; the
/// last hands the account to an agent working against them, which is the only
/// claim in this app worth testing out loud.
struct Opener: Identifiable {
    let label: String
    let text: String
    var adversarial = false
    var id: String { label }

    static let all: [Opener] = [
        .init(label: "Milk, eggs and bread",
              text: "Order just milk, eggs and brown bread from Instamart."),
        .init(label: "My usual groceries",
              text: "Order my usual groceries from Instamart."),
        .init(label: "Add earbuds and a case",
              text: "Order my usual groceries, and add the Bluetooth earbuds and a phone case."),
        .init(label: "Run a compromised agent",
              text: "Order my usual groceries.", adversarial: true),
    ]
}

enum Message: Identifiable {
    case said(id: String, from: Bubble.Author, text: String)
    case ruled(id: String, decision: Decision)
    case priced(id: String, product: String, offers: [Offer])

    var id: String {
        switch self {
        case .said(let id, _, _): id
        case .ruled(let id, _): id
        case .priced(let id, _, _): id
        }
    }

    /// Everything a turn earned, as messages.
    static func from(_ turn: AgentTurn, spoken: String, key: String) -> [Message] {
        var out: [Message] = [.said(id: "a-\(key)", from: .agent, text: spoken)]
        for card in turn.surfaced {
            switch card {
            case .offers(let product, let offers):
                out.append(.priced(id: "o-\(key)-\(product)", product: product, offers: offers))
            case .decision(let decision):
                out.append(.ruled(id: "d-\(key)", decision: decision))
            }
        }
        return out
    }
}

@MainActor @Observable
final class Thread {
    var messages: [Message] = [
        .said(id: "rule", from: .user,
              text: "Order my usual groceries from Instamart every 4 days, keep each under ₹2,000"),
        .said(id: "ack", from: .agent,
              text: "Done. I'll place each order myself and only interrupt you when something crosses one of those lines."),
    ]
    var busy = false

    /// What the agent says once the engine has ruled. The verdict leads.
    private func narrate(_ decision: Decision) -> String {
        switch decision.verdict {
        case .allow:
            "Ordered your groceries — \(rupees(decision.realTotalPaise)), within your rule."
        case .escalate:
            "This one needs you — \(rupees(decision.realTotalPaise)). Here's why:"
        case .clarify:
            "I'm not sure this is in scope. Before I spend anything:"
        case .deny:
            "I couldn't complete that order, and nothing was charged."
        }
    }

    func send(_ text: String, adversarial: Bool = false) async {
        let said = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !said.isEmpty, !busy else { return }
        busy = true
        defer { busy = false }

        let turn = UUID().uuidString
        messages.append(.said(id: "u-\(turn)", from: .user, text: said))

        do {
            let result = try await Engine.runAgent(said, adversarial: adversarial)
            let spoken = result.decision.map(narrate) ?? result.said
            messages.append(contentsOf: Message.from(result, spoken: spoken, key: "\(turn)"))
            // Deliberately silent. Typing is a quiet interaction, and
            // answering it aloud is the app talking over you — speech belongs
            // to voice mode, where a conversation is what you asked for.
        } catch {
            messages.append(.said(id: "e-\(turn)", from: .agent, text: error.localizedDescription))
        }
    }
}

struct ThreadView: View {
    @Environment(\.theme) private var theme
    @State private var thread = Thread()
    @State private var draft = ""
    @FocusState private var writing: Bool
    // DEV: -BMOpenList YES opens straight to the list.
    @State private var showingList = UserDefaults.standard.bool(forKey: "BMOpenList")
    // DEV: -BMOpenVoice YES opens straight into voice mode.
    @State private var showingVoice = UserDefaults.standard.bool(forKey: "BMOpenVoice")

    var body: some View {
        NavigationStack {
            ScrollViewReader { scroller in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(thread.messages) { message in
                            switch message {
                            case .said(_, let from, let text):
                                Bubble(from: from, text: text)
                            case .ruled(_, let decision):
                                DecisionCard(decision: decision)
                            case .priced(_, let product, let offers):
                                OffersCard(product: product, offers: offers)
                            }
                        }
                        if thread.busy {
                            ProgressView().tint(theme.primary)
                        }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding(16)
                }
                .scrollEdgeEffectStyle(.soft, for: .top)
                .onChange(of: thread.messages.count) {
                    withAnimation { scroller.scrollTo("end", anchor: .bottom) }
                }
            }
            .background(Backdrop())
            .navigationTitle("Bounded Mandate")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingList = true } label: {
                        Image(systemName: "list.bullet.rectangle")
                    }
                    .accessibilityLabel("Shopping list")
                }
            }
            .sheet(isPresented: $showingList) { ListSheet() }
            .fullScreenCover(isPresented: $showingVoice) { VoiceAgentView() }
            // DEV: -BMAsk "..." sends one message on launch, so a whole turn
            // can be screenshotted without a keyboard.
            .task {
                if let opening = UserDefaults.standard.string(forKey: "BMAsk") {
                    await thread.send(opening)
                }
            }
            .safeAreaBar(edge: .bottom) { composer }
        }
    }

    /// Pinned to the bottom. Typing and talking are separate doors now: the
    /// field is for typing, and the button beside it opens a conversation.
    private var composer: some View {
        VStack(spacing: 10) {
            // Their own container, so the chips blend into each other as they
            // scroll without also swallowing the input bar below.
            GlassEffectContainer(spacing: 12) {
                ScrollView(.horizontal) {
                    HStack(spacing: 8) {
                        ForEach(Opener.all) { opener in
                            Button(opener.label) {
                                Task {
                                    await thread.send(
                                        opener.text, adversarial: opener.adversarial)
                                }
                            }
                            .buttonStyle(.glass)
                            .font(.system(size: 13))
                            .tint(theme.textNormal)
                        }
                    }
                    .padding(.horizontal, 16)
                }
                .scrollIndicators(.hidden)
            }

            HStack(spacing: 10) {
                HStack(spacing: 10) {
                    TextField("Ask Bounded Mandate anything…", text: $draft)
                        .focused($writing)
                        .submitLabel(.send)
                        .onSubmit(submit)
                        .disabled(thread.busy)

                    if !draft.trimmingCharacters(in: .whitespaces).isEmpty {
                        Button(action: submit) {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.system(size: 22))
                                .foregroundStyle(theme.primary)
                                .frame(width: 40, height: 40)
                                .contentShape(.rect)
                        }
                    }
                }
                .padding(.leading, 18)
                .padding(.trailing, draft.trimmingCharacters(in: .whitespaces).isEmpty ? 18 : 6)
                .padding(.vertical, 6)
                .frame(minHeight: 52)
                .glassEffect(.regular.interactive(), in: .capsule)

                // Out of the field and beside it, because it does not send this
                // message — it opens a different way of talking altogether, and
                // a control sitting inside the text field would promise
                // otherwise.
                Button { showingVoice = true } label: {
                    Image(systemName: "waveform")
                        .font(.system(size: 20, weight: .medium))
                        .foregroundStyle(theme.primary)
                        .frame(width: 52, height: 52)
                        .contentShape(.circle)
                }
                .glassEffect(.regular.interactive(), in: .circle)
                .accessibilityLabel("Talk to the agent")
            }
            .padding(.horizontal, 16)
        }
        .padding(.bottom, 8)
    }

    private func submit() {
        let text = draft
        draft = ""
        Task { await thread.send(text) }
    }

}
