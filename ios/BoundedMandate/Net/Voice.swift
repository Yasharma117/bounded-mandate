import AVFoundation
import Observation
import SwiftUI

/// Voice goes through the engine's host, never straight to ElevenLabs: the key
/// stays server-side, so nothing sensitive ships inside the app bundle.
///
/// Speech is an *utterance*. It reaches the agent with exactly the standing
/// that typing has — the engine still decides, and no verdict is reachable by
/// voice that is not reachable by text.
enum Voice {
    /// Speak a line back. Failures are swallowed on purpose: losing audio
    /// should never cost the user a decision they can already read on screen.
    static func say(_ text: String) async {
        do {
            var request = URLRequest(url: Engine.baseURL.appending(path: "/api/voice/speak"))
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: ["text": text])

            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return }
            await Player.shared.play(data)
        } catch {
            // silent — the screen already said it
        }
    }

    /// Raw audio bytes up, text back.
    static func transcribe(_ audio: Data) async throws -> String {
        var request = URLRequest(url: Engine.baseURL.appending(path: "/api/voice/transcribe"))
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.httpBody = audio
        request.timeoutInterval = 60

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw Engine.Failure("No response") }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard http.statusCode == 200 else {
            throw Engine.Failure(json?["detail"] as? String ?? "Transcription failed")
        }
        return (json?["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Holds the player for the length of playback; replacing it stops an older
/// line talking over a newer one.
private actor Player {
    static let shared = Player()
    private var player: AVAudioPlayer?

    func play(_ data: Data) {
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
        try? AVAudioSession.sharedInstance().setActive(true)
        player = try? AVAudioPlayer(data: data)
        player?.play()
    }
}

@MainActor @Observable
final class VoiceRecorder {
    private(set) var listening = false
    private(set) var problem: String?

    private var recorder: AVAudioRecorder?
    private var file: URL?

    /// Start on the first tap, stop and transcribe on the second.
    func toggle() async -> String? {
        problem = nil
        if listening { return await stop() }
        await start()
        return nil
    }

    private func start() async {
        guard await AVAudioApplication.requestRecordPermission() else {
            problem = "Microphone access is off for this app."
            return
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .default)
            try AVAudioSession.sharedInstance().setActive(true)

            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("utterance.m4a")
            let recorder = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 44_100,
                AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ])
            recorder.record()
            self.recorder = recorder
            self.file = url
            listening = true
        } catch {
            problem = error.localizedDescription
        }
    }

    private func stop() async -> String? {
        listening = false
        recorder?.stop()
        recorder = nil
        guard let file, let audio = try? Data(contentsOf: file) else {
            problem = "Nothing was recorded."
            return nil
        }
        defer { try? FileManager.default.removeItem(at: file) }

        do {
            let heard = try await Voice.transcribe(audio)
            if heard.isEmpty { problem = "I didn't catch that." }
            return heard
        } catch {
            problem = error.localizedDescription
            return nil
        }
    }
}
