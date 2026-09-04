import AVFoundation
import Observation
import SwiftUI

/// A spoken conversation, rather than a recording you send.
///
/// **Push to talk.** The microphone is open while the circle is held and shut
/// the moment it is let go, so a turn is something you did rather than
/// something the room did. It ran itself once — listen until 1.4s of quiet,
/// transcribe, answer, listen again — which is the better interaction right up
/// until the person holding the phone is also talking to somebody else. Then
/// narration, a question from the room and a television all arrive as
/// utterances, and an agent that spends money should not take dictation from a
/// conversation it was never part of.
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
            case .idle: "Hold to talk"
            case .listening: "Listening — let go to send"
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
    /// cover tears that down, and the session's turn task is still running a
    /// round trip that then touches it. Swift traps that as
    /// `swift_abortRetainUnowned` and the app dies — `EXC_CRASH / SIGABRT`, no
    /// message, no recovery.
    ///
    /// Weak turns the same moment into nothing happening, which is the correct
    /// outcome: the screen the answer was going to is gone, so there is nowhere
    /// for it to land and no reason to shout about it.
    private weak var thread: Thread?

    init(thread: Thread) { self.thread = thread }


    private let audio = AudioIO()
    /// The turn in flight. One at a time — a press cancels the one before it.
    private var turn: Task<Void, Never>?
    /// True from press to release, and the only thing holding the mic open.
    private var held = false
    private var running = false
    /// Which service speaks. Changed live, so both can be judged by ear.
    private(set) var provider: String?
    /// What the engine can speak with, asked once when voice mode opens.
    private(set) var providers: [String] = []

    func use(provider name: String) { provider = name }

    /// Ask the engine which voices it has. The keys live there, so the list
    /// does too — the app has no way of knowing which are configured.
    func loadProviders() async {
        let answer = await Voice.providers()
        providers = answer.available
        if provider == nil { provider = answer.current }
    }

    // MARK: - a turn

    /// Opens voice mode: permission, the voices, and the audio session. It
    /// listens to nothing until you hold the circle.
    func start() async {
        guard !running else { return }
        problem = nil
        guard await AVAudioApplication.requestRecordPermission() else {
            problem = "Microphone access is off for this app."
            return
        }
        running = true
        await loadProviders()
        do {
            // Once per voice mode, not once per turn. Every hand-over used to
            // pay this again, which is why later turns felt worse than the
            // first.
            try await audio.begin()
        } catch {
            problem = error.localizedDescription
            stop()
        }
    }

    func stop() {
        running = false
        held = false
        turn?.cancel()
        turn = nil
        level = 0
        phase = .idle
        Task { await audio.end() }
    }

    /// Held. Everything from here until `release()` is the utterance.
    ///
    /// Pressing while it is still speaking interrupts it, which is what a
    /// person does. The new turn waits for the cancelled one to unwind rather
    /// than racing it, so the phase the screen shows is always this turn's.
    func press() {
        guard running, !held else { return }
        held = true
        let previous = turn
        previous?.cancel()
        turn = Task {
            await previous?.value
            await takeTurn()
        }
    }

    /// Let go. The recording stops within a meter tick and goes up as it is.
    func release() { held = false }

    private func takeTurn() async {
        await audio.stopPlaying()
        problem = nil
        phase = .listening

        do {
            _ = try await audio.startRecording()
        } catch {
            problem = error.localizedDescription
            phase = .idle
            return
        }

        while running, held, !Task.isCancelled {
            try? await Task.sleep(for: .milliseconds(50))
            guard let power = await audio.inputPower() else { break }
            // -60 dB is effectively silence, 0 is clipping. Squared so quiet
            // speech still moves the field without loud speech pinning it.
            let normalised = Double(max(0, (power + 60) / 60))
            level = smoothed(normalised * normalised)
        }

        phase = .thinking
        level = 0

        do {
            let captured = try await audio.finishRecording()
            // A tap rather than a hold: too short to be anything anybody said.
            guard running, !Task.isCancelled, captured.count > 4_000 else {
                phase = .idle
                return
            }
            let heard = try await Voice.transcribe(captured)
            // Held or not, the room can still be in the recording — a
            // television behind you does not stop for the press.
            if Voice.isSpeech(heard) { await answer(heard) }
        } catch is CancellationError {
        } catch {
            problem = error.localizedDescription
        }
        phase = .idle
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
