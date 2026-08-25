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

    var id: String {
        switch self {
        case .said(let id, _, _): id
        case .ruled(let id, _): id
        }
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
            messages.append(.said(id: "a-\(turn)", from: .agent, text: spoken))
            if let decision = result.decision {
                messages.append(.ruled(id: "d-\(turn)", decision: decision))
            }
            await Voice.say(spoken)
        } catch {
            messages.append(.said(id: "e-\(turn)", from: .agent, text: error.localizedDescription))
        }
    }
}

struct ThreadView: View {
    @Environment(\.theme) private var theme
    @State private var thread = Thread()
    @State private var draft = ""
    @State private var voice = VoiceRecorder()
    @FocusState private var writing: Bool
    // DEV: -BMOpenList YES opens straight to the list.
    @State private var showingList = UserDefaults.standard.bool(forKey: "BMOpenList")

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
                // DEV ONLY — goes with Gallery.swift before submission.
                ToolbarItem(placement: .topBarLeading) {
                    NavigationLink { Gallery() } label: {
                        Image(systemName: "rectangle.stack")
                    }
                }
            }
            .sheet(isPresented: $showingList) { ListSheet() }
            .safeAreaBar(edge: .bottom) { composer }
        }
    }

    /// Pinned to the bottom. Speaking and typing are the same input, and the
    /// keyboard is handled by the system rather than by hand.
    private var composer: some View {
        VStack(spacing: 10) {
                if let problem = voice.problem {
                    Text(problem)
                        .font(.system(size: 12))
                        .foregroundStyle(theme.negative)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 6)
                }

                // Their own container, so the chips blend into each other as
                // they scroll without also swallowing the input bar below.
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
                    TextField(
                        voice.listening ? "Listening…" : "Ask Bounded Mandate anything…",
                        text: $draft
                    )
                    .focused($writing)
                    .submitLabel(.send)
                    .onSubmit(submit)
                    .disabled(thread.busy || voice.listening)

                    if draft.trimmingCharacters(in: .whitespaces).isEmpty {
                        Button(action: toggleMic) {
                            Image(systemName: voice.listening ? "stop.circle.fill" : "mic.fill")
                                .font(.system(size: voice.listening ? 22 : 17))
                                .foregroundStyle(voice.listening ? theme.negative : theme.textMuted)
                                // The glyph is 17pt; the target must not be.
                                .frame(width: 44, height: 44)
                                .contentShape(.rect)
                        }
                    } else {
                        Button(action: submit) {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.system(size: 22))
                                .foregroundStyle(theme.primary)
                                .frame(width: 44, height: 44)
                                .contentShape(.rect)
                        }
                    }
                }
                .padding(.leading, 18)
                .padding(.trailing, 6)
                .padding(.vertical, 4)
                .glassEffect(.regular.interactive(), in: .capsule)
                .padding(.horizontal, 16)
        }
        .padding(.bottom, 8)
    }

    private func submit() {
        let text = draft
        draft = ""
        Task { await thread.send(text) }
    }

    private func toggleMic() {
        Task {
            // Straight through: speaking is how this app is meant to be used,
            // and a confirm step before every sentence is the friction it
            // exists to remove.
            if let heard = await voice.toggle(), !heard.isEmpty {
                await thread.send(heard)
            }
        }
    }
}
