import AVFoundation
import Observation
import SwiftUI

/// A spoken conversation, rather than a recording you send.
///
/// The loop runs itself: listen until you stop talking, transcribe, hand it to
/// the agent, speak the verdict, listen again. Nothing is pressed twice. That is
/// the difference between a microphone button and a voice mode, and it is the
/// only reason a hands-free grocery order is a believable thing to demo.
///
/// Turns land in the **same thread** the typed conversation uses. Voice is a
/// state of the conversation, not a separate screen: a duplicate transcript
/// would need its own layout for cards, and the first thing that layout did was
/// clip them.
///
/// This type holds state and nothing else. Every Core Audio call lives in
/// `AudioIO`, off the main thread, because doing it here froze the composer
/// morph for as long as the audio session took to come up.
@MainActor @Observable
final class VoiceSession {
    enum Phase: Equatable {
        case idle
        case listening
        case thinking
        case speaking

        var label: String {
            switch self {
            case .idle: "Tap to talk"
            case .listening: "Listening"
            case .thinking: "Working on it"
            case .speaking: "Speaking"
            }
        }
    }

    private(set) var phase: Phase = .idle
    /// 0…1, smoothed. Drives the backdrop, so the screen reacts to the room.
    private(set) var level: Double = 0
    private(set) var problem: String?

    /// Where turns go. The thread is the conversation; this only speaks into it.
    ///
    /// **Weak, not `unowned`.** `unowned` asserts the thread outlives the
    /// session, which is true right up until somebody closes the voice screen
    /// mid-turn: `ThreadView` owns the `Thread` in `@State`, dismissing the
    /// cover tears that down, and the session's `loop` task is still running a
    /// round trip that then touches it. Swift traps that as
    /// `swift_abortRetainUnowned` and the app dies — `EXC_CRASH / SIGABRT`, no
    /// message, no recovery.
    ///
    /// Weak turns the same moment into nothing happening, which is the correct
    /// outcome: the screen the answer was going to is gone, so there is nowhere
    /// for it to land and no reason to shout about it.
    private weak var thread: Thread?

    init(thread: Thread) { self.thread = thread }


    /// Stop after this much quiet **once you have actually said something**.
    private let silenceSeconds: TimeInterval = 1.4
    private let silenceThreshold: Float = -38
    /// How long to wait for a first word before admitting it cannot hear you.
    private let patienceSeconds: TimeInterval = 12

    private let audio = AudioIO()
    private var loop: Task<Void, Never>?
    private var quietSince: Date?
    private var heardSpeech = false
    private var openedMic: Date?
    private var running = false
    /// Which service speaks. Changed live, so both can be judged by ear.
    private(set) var provider: String?
    /// What the engine can speak with, asked once when voice mode opens.
    private(set) var providers: [String] = []

    var isActive: Bool { phase != .idle }

    func use(provider name: String) { provider = name }

    /// Ask the engine which voices it has. The keys live there, so the list
    /// does too — the app has no way of knowing which are configured.
    func loadProviders() async {
        let answer = await Voice.providers()
        providers = answer.available
        if provider == nil { provider = answer.current }
    }

    // MARK: - the loop

    func start() async {
        guard !running else { return }
        problem = nil
        guard await AVAudioApplication.requestRecordPermission() else {
            problem = "Microphone access is off for this app."
            return
        }
        running = true
        await loadProviders()
        // The whole conversation is one task. Cancelling it is how it stops,
        // which means no path can leave a half-configured session behind.
        loop = Task { await converse() }
    }

    func stop() {
        running = false
        loop?.cancel()
        loop = nil
        quietSince = nil
        level = 0
        phase = .idle
        Task { await audio.end() }
    }

    private func converse() async {
        do {
            // Once per voice mode, not once per turn. Every hand-over used to
            // pay this again, which is why later turns felt worse than the
            // first.
            try await audio.begin()
        } catch {
            problem = error.localizedDescription
            stop()
            return
        }

        while running, !Task.isCancelled {
            guard let heard = await listen() else { break }
            guard Voice.isSpeech(heard) else { continue }
            await answer(heard)
        }
    }

    /// Records until the speaker goes quiet, then transcribes. `nil` ends the
    /// conversation; an empty string means "nothing worth sending, go again".
    private func listen() async -> String? {
        phase = .listening
        quietSince = nil
        heardSpeech = false
        openedMic = Date()

        do {
            _ = try await audio.startRecording()
        } catch {
            problem = error.localizedDescription
            return nil
        }

        while running, !Task.isCancelled {
            try? await Task.sleep(for: .milliseconds(50))
            guard let power = await audio.inputPower() else { break }
            if await meter(power) { break }
        }
        guard running, !Task.isCancelled else { return nil }

        phase = .thinking
        level = 0

        do {
            let captured = try await audio.finishRecording()
            // Shorter than this is the room moving, not a person.
            guard captured.count > 4_000 else { return "" }
            return try await Voice.transcribe(captured)
        } catch is CancellationError {
            return nil
        } catch {
            problem = error.localizedDescription
            return ""
        }
    }

    /// Returns true when the turn is over. Also drives the backdrop.
    private func meter(_ power: Float) async -> Bool {
        // -60 dB is effectively silence, 0 is clipping. Squared so quiet speech
        // still moves the field without loud speech pinning it.
        let normalised = Double(max(0, (power + 60) / 60))
        level = smoothed(normalised * normalised)

        guard power < silenceThreshold else {
            heardSpeech = true
            quietSince = nil
            return false
        }

        // Nothing said yet: this is the pause before you start, not the pause
        // after you finish. Waiting is correct — up to a point, because silence
        // forever and a broken microphone look identical from the outside.
        guard heardSpeech else {
            if let opened = openedMic, Date().timeIntervalSince(opened) >= patienceSeconds {
                problem = "I can't hear anything. Check the microphone, then tap to try again."
                stop()
            }
            return false
        }

        let since = quietSince ?? Date()
        quietSince = since
        return Date().timeIntervalSince(since) >= silenceSeconds
    }

    private func answer(_ heard: String) async {
        let key = UUID().uuidString
        // Gone means the screen was dismissed while this turn was in flight.
        // Nothing to append to and nothing to say — stop rather than continue
        // into a round trip whose answer has nowhere to go.
        guard let thread else {
            stop()
            return
        }
        thread.append(.said(id: "u-\(key)", from: .user, text: heard))

        do {
            let result = try await Engine.runAgent(heard, history: thread.spokenSoFar)
            // Its own words, not a canned line. `narrate` used to override the
            // agent whenever a decision existed, so every order sounded
            // identical to every other one — which is the opposite of a
            // conversation. It is the fallback now, for when the agent says
            // nothing at all.
            let spoken = result.said.isEmpty ? (result.decision.map(Self.narrate) ?? "") : result.said
            // Cards arrive as the conversation earns them. Spoken numbers are
            // the thing a voice interface is worst at, so prices land on screen
            // rather than only in the air.
            thread.append(contentsOf: Message.from(result, spoken: spoken, key: key))
            await say(spoken)
        } catch is CancellationError {
            return
        } catch {
            problem = error.localizedDescription
        }
    }

    /// Speaks, driving the backdrop from playback so the field keeps moving
    /// while the agent talks — silence there would read as a dropped call.
    private func say(_ text: String) async {
        guard running, let spoken = await Voice.audio(for: text, provider: provider) else {
            return
        }
        phase = .speaking
        do {
            try await audio.startPlaying(spoken)
        } catch {
            problem = error.localizedDescription
            return
        }
        while running, !Task.isCancelled, let power = await audio.outputPower() {
            level = smoothed(Double(max(0, (power + 50) / 50)))
            try? await Task.sleep(for: .milliseconds(50))
        }
        await audio.stopPlaying()
        level = 0
    }

    /// Smooth the meter here rather than by animating it in the view.
    ///
    /// This value changes twenty times a second; attaching a 200ms animation to
    /// it stacks twenty overlapping animations per second, which costs frames
    /// and makes the result mushy rather than smooth. An exponential average
    /// gives the same softness for one multiply.
    private func smoothed(_ sample: Double) -> Double {
        // Quick to react, slow to fall — a voice that stops should leave the
        // field settling rather than dropping out from under itself.
        let weight = sample > level ? 0.55 : 0.18
        return level + (sample - level) * weight
    }

    static func narrate(_ decision: Decision) -> String {
        switch decision.verdict {
        case .allow:
            "Ordered your groceries — \(rupees(decision.realTotalPaise)), within your rule."
        case .escalate:
            "This one needs you — \(rupees(decision.realTotalPaise)). Here's why."
        case .clarify:
            "I'm not sure this is in scope. Before I spend anything, have a look."
        case .deny:
            "I couldn't complete that order, and nothing was charged."
        }
    }
}
