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

    /// Voice speaks into the same thread typing does. There is one
    /// conversation; the microphone is a way into it, not a place of its own.
    /// How long the message at this index waits before arriving.
    ///
    /// Only the last turn staggers. Everything already on screen has a delay of
    /// zero, so scrolling back through history does not replay an entrance.
    func delay(for index: Int) -> TimeInterval {
        let fromEnd = messages.count - 1 - index
        guard fromEnd >= 0, fromEnd < turnLength else { return 0 }
        return TimeInterval(turnLength - 1 - fromEnd) * Motion.stagger
    }

    /// How many messages the most recent turn produced.
    private(set) var turnLength = 0

    func append(_ message: Message) {
        turnLength = 1
        messages.append(message)
    }
    func append(contentsOf added: [Message]) {
        turnLength = added.count
        messages.append(contentsOf: added)
    }

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
        append(.said(id: "u-\(turn)", from: .user, text: said))

        do {
            let result = try await Engine.runAgent(said, adversarial: adversarial)
            let spoken = result.decision.map(narrate) ?? result.said
            append(contentsOf: Message.from(result, spoken: spoken, key: "\(turn)"))
            // Deliberately silent. Typing is a quiet interaction, and
            // answering it aloud is the app talking over you — speech belongs
            // to voice mode, where a conversation is what you asked for.
        } catch {
            append(.said(id: "e-\(turn)", from: .agent, text: error.localizedDescription))
        }
    }
}

struct ThreadView: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var thread = Thread()
    @State private var draft = ""
    @FocusState private var writing: Bool
    // DEV: -BMOpenList YES opens straight to the list.
    @State private var showingList = UserDefaults.standard.bool(forKey: "BMOpenList")
    // DEV: -BMOpenVoice YES starts in voice mode.
    @State private var voice: VoiceSession?
    @Namespace private var glass
    /// One bump per state change, which is what the ripple listens to.
    @State private var pulse = 0

    var body: some View {
        NavigationStack {
            ScrollViewReader { scroller in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(Array(thread.messages.enumerated()), id: \.element.id) {
                            index, message in
                            Group {
                                switch message {
                                case .said(_, let from, let text):
                                    Bubble(from: from, text: text)
                                case .ruled(_, let decision):
                                    DecisionCard(decision: decision)
                                case .priced(_, let product, let offers):
                                    OffersCard(product: product, offers: offers)
                                }
                            }
                            // One turn can produce a sentence and a card. They
                            // land in sequence rather than together, so the eye
                            // reads the answer before the evidence.
                            .arrives(reduceMotion, delay: thread.delay(for: index))
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
                    // Slightly behind the entrance, so the new thing is on
                    // screen by the time the scroll catches up to it.
                    withAnimation(Motion.move(0.34).delay(Motion.stagger)) {
                        scroller.scrollTo("end", anchor: .bottom)
                    }
                }
            }
            .background(
                ZStack {
                    Backdrop(intensity: voice?.level ?? 0, vivid: voice != nil)
                    Ripple(trigger: pulse, color: theme.primary)
                }
            )
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
            // One ring per hand-over: you stopped, it is thinking, it is
            // speaking. Voice has no cursor, so the screen has to say so.
            .onChange(of: voice?.phase) { pulse += 1 }
            // DEV: -BMAsk "..." sends one message on launch, so a whole turn
            // can be screenshotted without a keyboard.
            .task {
                if UserDefaults.standard.bool(forKey: "BMOpenVoice") { startTalking() }
                if let opening = UserDefaults.standard.string(forKey: "BMAsk") {
                    await thread.send(opening)
                }
            }
            .safeAreaBar(edge: .bottom) { composer }
        }
    }

    /// Pinned to the bottom.
    ///
    /// Voice is a *state of this bar*, not another screen. Tapping the waveform
    /// morphs the field into an orb and the thread stays exactly where it is —
    /// which is the point, because the cards a conversation produces already
    /// have a home here, and the duplicate transcript that used to exist had to
    /// invent a worse one.
    private var composer: some View {
        VStack(spacing: 10) {
            if voice == nil {
                openers
            } else if let problem = voice?.problem {
                Text(problem)
                    .font(.system(size: 12))
                    .foregroundStyle(theme.negative)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            } else {
                Text(voice?.phase.label ?? "")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(theme.textSubtle)
                    .contentTransition(.opacity)
                    .animation(Motion.enter(0.2), value: voice?.phase)
            }

            GlassEffectContainer(spacing: 22) {
                HStack(spacing: 10) {
                    if let voice {
                        orb(voice)
                    } else {
                        field
                    }
                }
                .padding(.horizontal, 16)
            }
        }
        .padding(.bottom, 8)
        .animation(
            Motion.respectful(voice == nil ? Motion.unmorph : Motion.morph, reduced: reduceMotion),
            value: voice == nil
        )
    }

    private var openers: some View {
        GlassEffectContainer(spacing: 12) {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(Opener.all) { opener in
                        Button(opener.label) {
                            Task {
                                await thread.send(opener.text, adversarial: opener.adversarial)
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
    }

    private var field: some View {
        Group {
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
            .glassEffectID("composer", in: glass)

            Button { startTalking() } label: {
                Image(systemName: "waveform")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(theme.primary)
                    .frame(width: 52, height: 52)
                    .contentShape(.circle)
            }
            .glassEffect(.regular.interactive(), in: .circle)
            .glassEffectID("mic", in: glass)
            .accessibilityLabel("Talk to the agent")
        }
    }

    /// The field, grown. Same glass, same identity — so it stretches into place
    /// rather than one control vanishing and another appearing.
    private func orb(_ session: VoiceSession) -> some View {
        Button { stopTalking() } label: {
            ZStack {
                Circle()
                    .fill(theme.primary.opacity(0.14))
                    // 1.22 rather than 1.42: at the old size a loud syllable
                    // made the control jump, which reads as instability rather
                    // than as listening.
                    .scaleEffect(reduceMotion ? 1 : 1 + session.level * 0.22)
                Image(systemName: symbol(for: session.phase))
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(theme.textNormal)
                    .symbolEffect(.variableColor, isActive: session.phase == .thinking)
            }
            .frame(width: 96, height: 96)
            .contentShape(.circle)
        }
        .buttonStyle(.pressable)
        .glassEffect(.regular.interactive(), in: .circle)
        .glassEffectID("composer", in: glass)
        .animation(Motion.respectful(Motion.follow, reduced: reduceMotion), value: session.level)
        .accessibilityLabel("Stop talking")
    }

    private func symbol(for phase: VoiceSession.Phase) -> String {
        switch phase {
        case .idle, .listening: "waveform"
        case .thinking: "ellipsis"
        case .speaking: "speaker.wave.2.fill"
        }
    }

    private func startTalking() {
        writing = false
        let session = VoiceSession(thread: thread)
        voice = session
        pulse += 1
        Task { await session.start() }
    }

    private func stopTalking() {
        voice?.stop()
        voice = nil
        pulse += 1
    }

    private func submit() {
        let text = draft
        draft = ""
        Task { await thread.send(text) }
    }

}
