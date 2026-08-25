import Foundation

/// Voice goes through the engine's host, never straight to a provider: the keys
/// stay server-side, so nothing sensitive ships inside the app bundle.
///
/// Speech is an *utterance*. It reaches the agent with exactly the standing that
/// typing has — the engine still decides, and no verdict is reachable by voice
/// that is not reachable by text.
enum Voice {
    /// Raw audio bytes up, text back. Hearing is always ElevenLabs Scribe.
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

    /// Synthesised audio for a line, or nil.
    ///
    /// Returns bytes rather than playing them, because the caller drives the
    /// backdrop from playback level and needs to own the player. Failures are
    /// swallowed on purpose: losing audio should never cost the user a decision
    /// they can already read on screen.
    ///
    /// The two services disagree on format — mp3 from ElevenLabs, wav from
    /// Rumik — and `AVAudioPlayer` sniffs either, so which one spoke is only
    /// ever informational here.
    static func audio(for text: String, provider: String? = nil) async -> Data? {
        do {
            var request = URLRequest(url: Engine.baseURL.appending(path: "/api/voice/speak"))
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            var body: [String: Any] = ["text": text]
            if let provider { body["provider"] = provider }
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.timeoutInterval = 60

            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return data
        } catch {
            return nil  // silent — the screen already said it
        }
    }

    /// Was that a person, or the room?
    ///
    /// Scribe tags non-speech audio as `[music]`, `[outro jingle]`, `[silence]`
    /// and so on. Those arrive looking exactly like an utterance, and a voice
    /// mode that forwards them lets a television in the background talk to an
    /// agent that spends money. Nothing bracketed is an instruction, and a
    /// couple of stray syllables is not one either.
    static func isSpeech(_ transcript: String) -> Bool {
        let stripped = transcript.replacingOccurrences(
            of: "\\[[^\\]]*\\]", with: " ", options: .regularExpression
        ).trimmingCharacters(in: .whitespacesAndNewlines)

        guard !stripped.isEmpty else { return false }
        // Two words is the shortest thing anyone says to a shopping agent, and
        // it keeps a cough or a single mis-heard syllable out of the loop.
        let words = stripped.split { !$0.isLetter && !$0.isNumber }
        return words.count >= 2
    }

    /// Which services can speak, for the picker in voice mode.
    static func providers() async -> (available: [String], current: String) {
        struct Wrapper: Decodable {
            let providers: [String]
            let `default`: String
        }
        do {
            let url = Engine.baseURL.appending(path: "/api/voice/providers")
            let (data, _) = try await URLSession.shared.data(from: url)
            let wrapper = try JSONDecoder().decode(Wrapper.self, from: data)
            return (wrapper.providers, wrapper.default)
        } catch {
            return ([], "")
        }
    }
}
