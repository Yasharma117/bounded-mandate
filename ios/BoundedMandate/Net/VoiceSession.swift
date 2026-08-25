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
/// Speech is still an **utterance**. It reaches the agent with exactly the
/// standing that typing has, and the engine decides either way — a voice channel
/// widens what can be *said*, never what will be *authorised*.
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
    /// Voice mode keeps its own turns so the transcript and the cards can be
    /// laid out for a screen you are not holding close.
    private(set) var turns: [Message] = []

    /// Stop after this much quiet. Long enough to think mid-sentence, short
    /// enough that finishing a sentence ends your turn.
    private let silenceSeconds: TimeInterval = 1.4
    private let silenceThreshold: Float = -38

    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var meter: Task<Void, Never>?
    private var quietSince: Date?
    private var running = false
    /// Which service speaks. Changed live, so both can be judged by ear.
    private var provider: String?

    var isActive: Bool { phase != .idle }

    func use(provider name: String) { provider = name }

    // MARK: - the loop

    func start() async {
        guard !running else { return }
        problem = nil
        guard await AVAudioApplication.requestRecordPermission() else {
            problem = "Microphone access is off for this app."
            return
        }
        running = true
        await listen()
    }

    func stop() {
        running = false
        meter?.cancel()
        meter = nil
        recorder?.stop()
        recorder = nil
        player?.stop()
        player = nil
        quietSince = nil
        level = 0
        phase = .idle
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func listen() async {
        guard running else { return }
        do {
            try AVAudioSession.sharedInstance().setCategory(
                .playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetooth]
            )
            try AVAudioSession.sharedInstance().setActive(true)

            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("turn-\(turns.count).m4a")
            let recorder = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 44_100,
                AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ])
            recorder.isMeteringEnabled = true
            recorder.record()
            self.recorder = recorder
            phase = .listening
            quietSince = nil
            startMetering()
        } catch {
            problem = error.localizedDescription
            stop()
        }
    }

    /// Polls the input level, both to drive the backdrop and to notice that the
    /// speaker has finished. Cheap enough at 20 Hz that a real VAD would be
    /// ceremony — this only has to tell speech from a quiet room.
    ///
    /// The turn is finished *inside* this task rather than by cancelling it.
    /// Cancelling the task that is doing the work makes its own next `await`
    /// throw, which surfaced to the user as the word "cancelled".
    private func startMetering() {
        meter?.cancel()
        meter = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(50))
                guard let self else { return }
                if await self.sampleLevel() {
                    await self.finishTurn()
                    return
                }
            }
        }
    }

    /// Returns true when the speaker has gone quiet for long enough.
    private func sampleLevel() async -> Bool {
        guard let recorder, phase == .listening else { return false }
        recorder.updateMeters()
        let power = recorder.averagePower(forChannel: 0)
        // -60 dB is effectively silence, 0 is clipping. Squared so quiet speech
        // still moves the field without loud speech pinning it.
        let normalised = Double(max(0, (power + 60) / 60))
        level = normalised * normalised

        guard power < silenceThreshold else {
            quietSince = nil
            return false
        }
        let since = quietSince ?? Date()
        quietSince = since
        return Date().timeIntervalSince(since) >= silenceSeconds
    }

    private func finishTurn() async {
        meter = nil
        guard let recorder else { return }
        recorder.stop()
        let url = recorder.url
        self.recorder = nil
        level = 0
        phase = .thinking

        defer { try? FileManager.default.removeItem(at: url) }
        guard let audio = try? Data(contentsOf: url), audio.count > 4_000 else {
            // Too short to be speech — the room moved, not a person. Resume.
            await listen()
            return
        }

        do {
            let heard = try await Voice.transcribe(audio)
            // The room is not a user. Anything that is not speech goes back to
            // listening without ever reaching the agent.
            guard Voice.isSpeech(heard) else {
                await listen()
                return
            }
            turns.append(.said(id: "u-\(UUID().uuidString)", from: .user, text: heard))

            let result = try await Engine.runAgent(heard)
            let spoken = result.decision.map(Self.narrate) ?? result.said
            turns.append(.said(id: "a-\(UUID().uuidString)", from: .agent, text: spoken))
            if let decision = result.decision {
                turns.append(.ruled(id: "d-\(UUID().uuidString)", decision: decision))
            }
            await say(spoken)
        } catch is CancellationError {
            return  // the session was stopped mid-turn; not a fault to report
        } catch {
            problem = error.localizedDescription
        }

        await listen()
    }

    /// Speaks, and drives the backdrop from playback so the field keeps moving
    /// while the agent talks — silence there would read as a dropped call.
    private func say(_ text: String) async {
        guard running, let spoken = await Voice.audio(for: text, provider: provider) else {
            return
        }
        phase = .speaking
        do {
            let player = try AVAudioPlayer(data: spoken)
            player.isMeteringEnabled = true
            player.play()
            self.player = player

            // Driving the field from playback keeps it alive while the agent
            // talks — a still screen here reads as a dropped call.
            while running, player.isPlaying {
                player.updateMeters()
                let power = player.averagePower(forChannel: 0)
                level = Double(max(0, (power + 50) / 50))
                try? await Task.sleep(for: .milliseconds(50))
            }
        } catch {
            problem = error.localizedDescription
        }
        player = nil
        level = 0
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
