import SwiftUI

/// Talking to the agent, rather than sending it a recording.
///
/// The screen has almost nothing on it at first, and that is deliberate: before
/// you have said anything there is nothing true to show. What it does instead is
/// *react* — the field brightens and moves with your voice, so an empty screen
/// still reads as listening. Cards arrive only as the conversation earns them.
struct VoiceAgentView: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    @State private var session = VoiceSession()
    @State private var providers: [String] = []
    @State private var provider = ""

    var body: some View {
        ZStack {
            Backdrop(intensity: session.level, vivid: true)

            VStack(spacing: 0) {
                header
                transcript
                Spacer(minLength: 0)
                controls
            }
        }
        .task {
            let known = await Voice.providers()
            providers = known.available
            provider = known.current
            await session.start()
        }
        .onDisappear { session.stop() }
    }

    private var header: some View {
        HStack {
            Button { dismiss() } label: {
                Image(systemName: "chevron.down")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(theme.textNormal)
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .glassEffect(.regular.interactive(), in: .circle)

            Spacer()

            // Both services are wired, so which one is speaking is a choice
            // made by ear rather than a setting buried in a file.
            if providers.count > 1 {
                Menu {
                    Picker("Voice", selection: $provider) {
                        ForEach(providers, id: \.self) { Text($0.capitalized).tag($0) }
                    }
                } label: {
                    HStack(spacing: 5) {
                        Text(provider.capitalized)
                            .font(.system(size: 13, weight: .medium))
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.system(size: 9, weight: .semibold))
                    }
                    .foregroundStyle(theme.textNormal)
                    .padding(.horizontal, 14)
                    .frame(height: 44)
                }
                .glassEffect(.regular.interactive(), in: .capsule)
                .onChange(of: provider) { _, chosen in session.use(provider: chosen) }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    /// The conversation so far. Cards come up as it progresses, which is the
    /// only thing on this screen that is worth reading twice.
    private var transcript: some View {
        ScrollViewReader { scroller in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(session.turns) { turn in
                        switch turn {
                        case .said(_, let from, let text):
                            Bubble(from: from, text: text)
                        case .ruled(_, let decision):
                            DecisionCard(decision: decision)
                        }
                    }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding(16)
            }
            .scrollIndicators(.hidden)
            .onChange(of: session.turns.count) {
                withAnimation { scroller.scrollTo("end", anchor: .bottom) }
            }
        }
    }

    private var controls: some View {
        VStack(spacing: 16) {
            if let problem = session.problem {
                Text(problem)
                    .font(.system(size: 13))
                    .foregroundStyle(theme.negative)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            Text(session.phase.label)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(theme.textSubtle)
                .contentTransition(.opacity)
                .animation(.easeInOut(duration: 0.2), value: session.phase)

            Orb(level: session.level, phase: session.phase) {
                if session.isActive {
                    session.stop()
                } else {
                    Task { await session.start() }
                }
            }
        }
        .padding(.bottom, 40)
    }
}

/// The one control. It breathes with whatever is making sound — your voice while
/// it listens, the agent's while it speaks — so the screen never looks frozen
/// during the seconds where nothing has been decided yet.
private struct Orb: View {
    @Environment(\.theme) private var theme
    let level: Double
    let phase: VoiceSession.Phase
    let onTap: () -> Void

    private var symbol: String {
        switch phase {
        case .idle: "waveform"
        case .listening: "waveform"
        case .thinking: "ellipsis"
        case .speaking: "speaker.wave.2.fill"
        }
    }

    var body: some View {
        Button(action: onTap) {
            ZStack {
                // Two rings so the reaction is visible at a glance from across
                // a desk, which is where a hands-free mode is actually used.
                Circle()
                    .fill(theme.primary.opacity(0.16))
                    .frame(width: 108, height: 108)
                    .scaleEffect(1 + level * 0.5)
                Circle()
                    .fill(theme.primary.opacity(0.22))
                    .frame(width: 88, height: 88)
                    .scaleEffect(1 + level * 0.28)

                Image(systemName: symbol)
                    .font(.system(size: 28, weight: .medium))
                    .foregroundStyle(theme.textNormal)
                    .symbolEffect(.variableColor, isActive: phase == .thinking)
                    .frame(width: 76, height: 76)
                    .glassEffect(.regular.interactive(), in: .circle)
            }
            .frame(width: 120, height: 120)
            .contentShape(.circle)
        }
        .buttonStyle(.plain)
        .animation(.easeOut(duration: 0.12), value: level)
        .accessibilityLabel(phase == .idle ? "Start talking" : "Stop")
    }
}
