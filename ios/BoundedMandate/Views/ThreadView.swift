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
    /// How long each message waited before arriving, decided once at append
    /// time. Computing it per row per render meant every scroll frame did work
    /// proportional to the whole thread, for a value that only matters in the
    /// instant a message appears.
    private(set) var delays: [String: TimeInterval] = [:]

    func append(_ message: Message) {
        messages.append(message)
    }
    /// One turn can produce a sentence and a card. They land in sequence rather
    /// than together, so the eye reads the answer before the evidence.
    func append(contentsOf added: [Message]) {
        for (offset, message) in added.enumerated() {
            delays[message.id] = TimeInterval(offset) * Motion.stagger
        }
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

    /// The conversation so far, as plain turns.
    ///
    /// Only what was *said*. Cards are not sent: a cart id from three turns ago
    /// is a reference the agent could charge against long after the basket
    /// stopped existing, and the engine would then be refusing a cart nobody
    /// meant to propose.
    var spokenSoFar: [[String: String]] {
        messages.suffix(12).compactMap { message in
            guard case .said(_, let from, let text) = message else { return nil }
            return ["from": from == .user ? "user" : "agent", "text": text]
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
            let result = try await Engine.runAgent(
                said, history: spokenSoFar, adversarial: adversarial
            )
            let spoken = result.said.isEmpty ? (result.decision.map(narrate) ?? "") : result.said
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
    @Environment(\.dismiss) private var dismiss
    /// Opened from the waveform rather than the field. Voice is still a state
    /// of the composer, not a screen — this only decides which state it opens in.
    var startInVoice = false
    @State private var thread = Thread()
    @State private var draft = ""
    @FocusState private var writing: Bool
    @State private var showingList = false
    @State private var showingAddress = false
    @State private var voice: VoiceSession?
    @Namespace private var glass
    /// One bump per state change, which is what the ripple listens to.
    @State private var pulse = 0
    /// 0 in the thread, 1 in voice mode. Animated rather than flipped, so the
    /// background arrives with the morph instead of cutting to blue a frame
    /// before it.
    @State private var voiceness: Double = 0


    var body: some View {
        NavigationStack {
            ScrollViewReader { scroller in
                ScrollView {
                    // Not lazy, deliberately. A transition inside a LazyVStack
                    // fires when a row is *realised*, so scrolling back through
                    // history replayed entrances on things that arrived minutes
                    // ago — which is what made it look broken.
                    //
                    // ponytail: fine to tens of messages; go back to lazy with
                    // an explicit "recently arrived" set if a thread ever gets
                    // long enough to matter.
                    VStack(alignment: .leading, spacing: 14) {
                        ForEach(thread.messages) { message in
                            row(message)
                        }
                        if thread.busy {
                            ProgressView().tint(theme.primary)
                        }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding(16)
                }
                // Hard, not soft. A soft edge lets the thread ghost up through
                // the title, and a money figure half-visible behind a nav bar
                // is worse than one that simply is not there yet.
                .scrollEdgeEffectStyle(.hard, for: .top)
                // The backdrop is behind the whole stack now, so everything in
                // front of it has to actually be transparent or it paints over
                // it — which showed up as a white page above the scrim.

                .onChange(of: thread.messages.count) {
                    // Slightly behind the entrance, so the new thing is on
                    // screen by the time the scroll catches up to it.
                    withAnimation(Motion.move(0.34).delay(Motion.stagger)) {
                        scroller.scrollTo("end", anchor: .bottom)
                    }
                }
            }
            .background(backdrop)
            .navigationTitle("Bounded Mandate")
            .navigationBarTitleDisplayMode(.inline)

            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { dismiss() } label: {
                        Image(systemName: "chevron.left")
                    }
                    .accessibilityLabel("Back")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingList = true } label: {
                        Image(systemName: "list.bullet.rectangle")
                    }
                    .accessibilityLabel("Shopping list")
                }
            }
            .sheet(isPresented: $showingList) { ListSheet() }
            .sheet(isPresented: $showingAddress) { AddressSheet() }
            // One ring per hand-over: you stopped, it is thinking, it is
            // speaking. Voice has no cursor, so the screen has to say so.
            .onChange(of: voice?.phase) { pulse += 1 }
            .task { if startInVoice, voice == nil { startTalking() } }
            // `safeAreaBar` paints its own glass, which sat as a translucent
            // slab behind the orb. The composer already carries its own glass,
            // so it takes the plain inset and measures itself instead.
            // A reserved inset, not an overlay: an overlay floats over the
            // thread, and the orb ended up sitting on top of a card the reader
            // was in the middle of.
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
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
                    .transition(.opacity.animation(Motion.enter(0.14)))
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
        // Full width, or the scrim takes the width of whichever child happens
        // to be widest and leaves rectangular seams across the thread.
        .frame(maxWidth: .infinity)
        // Content scrolling past has to *go* somewhere, and glass refracts
        // whatever is behind it — which here was the reader's own cart, showing
        // through the control they were about to press. The scrim fades the
        // thread into the page colour instead, over a long enough distance that
        // there is no edge to see, and follows the backdrop into voice mode so
        // the two never disagree about what colour the page is.
        .background(alignment: .bottom) { scrim }
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
            // The glass shape morphs; its *contents* do not. Letting the
            // placeholder ride the morph left a "Mandat…" ghost smeared across
            // the transition — text has no business being interpolated into a
            // circle. It leaves first, quickly, and the shape follows.
            .opacity(voice == nil ? 1 : 0)
            .animation(Motion.enter(0.12), value: voice == nil)
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
                    // than as listening. The level is already smoothed in the
                    // session, so this reads it straight — animating a value
                    // that changes twenty times a second just stacks twenty
                    // overlapping animations and costs frames.
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
        .accessibilityLabel("Stop talking")
    }

    private func symbol(for phase: VoiceSession.Phase) -> String {
        switch phase {
        case .idle, .listening: "waveform"
        case .thinking: "ellipsis"
        case .speaking: "speaker.wave.2.fill"
        }
    }

    @ViewBuilder
    private func row(_ message: Message) -> some View {
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
        .arrives(reduceMotion, delay: thread.delays[message.id] ?? 0)
    }

    /// The page colour under the composer, wherever the backdrop currently is.
    private var scrimColour: Color {
        theme.bgSubtle.mix(with: theme.primary, by: voiceness * 0.62)
    }

    private var scrim: some View {
        LinearGradient(
            stops: [
                .init(color: scrimColour.opacity(0), location: 0),
                .init(color: scrimColour.opacity(0.72), location: 0.38),
                .init(color: scrimColour, location: 0.72),
                .init(color: scrimColour, location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(height: 320)
        .frame(maxWidth: .infinity)
        // Overshoot the bottom rather than trusting the safe area to be
        // ignored from inside a background: it stopped at the home-indicator
        // boundary and left a visible band of untouched backdrop below it.
        .padding(.bottom, -140)
        .allowsHitTesting(false)
    }

    private var backdrop: some View {
        ZStack {
            Backdrop(voiceness: voiceness, level: voice?.level ?? 0)
            Ripple(trigger: pulse, color: theme.primary)
        }
        // As a plain `.background` this is sized to the scroll view's *content*
        // area, which is inset about 34pt on each side — so the page showed
        // through down both edges as a lighter rectangle the width of the
        // thread. Overshooting covers it; ignoresSafeArea alone does not,
        // because the inset is not safe area.
        .padding(.horizontal, -80)
        .ignoresSafeArea()
    }

    private func startTalking() {
        writing = false
        let session = VoiceSession(thread: thread)
        voice = session
        pulse += 1
        withAnimation(Motion.respectful(Motion.morph, reduced: reduceMotion)) { voiceness = 1 }
        Task { await session.start() }
    }

    private func stopTalking() {
        voice?.stop()
        voice = nil
        pulse += 1
        withAnimation(Motion.respectful(Motion.unmorph, reduced: reduceMotion)) { voiceness = 0 }
    }

    private func submit() {
        let text = draft
        draft = ""
        Task { await thread.send(text) }
    }

}
