import AVFoundation

/// Everything that touches Core Audio, off the main thread.
///
/// `AVAudioSession.setActive`, the recorder's initialiser and `record()` are all
/// synchronous and all slow — measured at 125ms together, and that is on top of
/// whatever route discovery costs. Running them on the main actor froze the
/// composer morph mid-flight, which is the visible half of the problem this
/// actor exists to solve.
///
/// The session is configured **once per voice mode**, not once per turn. Every
/// hand-over used to pay the full setup cost again, which is why the third turn
/// felt worse than the first.
actor AudioIO {
    enum Failure: LocalizedError {
        case noRecording
        var errorDescription: String? { "Nothing was recorded." }
    }

    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var configured = false

    /// Called once when voice mode opens.
    func begin() throws {
        guard !configured else { return }
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(
            .playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetooth]
        )
        try session.setActive(true)
        configured = true
    }

    /// Called once when voice mode closes.
    func end() {
        recorder?.stop()
        recorder = nil
        player?.stop()
        player = nil
        guard configured else { return }
        configured = false
        try? AVAudioSession.sharedInstance()
            .setActive(false, options: .notifyOthersOnDeactivation)
    }

    func startRecording() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("turn-\(UUID().uuidString).m4a")
        let recorder = try AVAudioRecorder(url: url, settings: [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ])
        recorder.isMeteringEnabled = true
        recorder.record()
        self.recorder = recorder
        return url
    }

    /// Input level in dB, or nil when nothing is recording.
    func inputPower() -> Float? {
        guard let recorder else { return nil }
        recorder.updateMeters()
        return recorder.averagePower(forChannel: 0)
    }

    /// Stops and hands back what was captured.
    func finishRecording() throws -> Data {
        guard let recorder else { throw Failure.noRecording }
        let url = recorder.url
        recorder.stop()
        self.recorder = nil
        defer { try? FileManager.default.removeItem(at: url) }
        return try Data(contentsOf: url)
    }

    func startPlaying(_ audio: Data) throws {
        let player = try AVAudioPlayer(data: audio)
        player.isMeteringEnabled = true
        player.play()
        self.player = player
    }

    /// Playback level in dB, or nil once it has finished.
    func outputPower() -> Float? {
        guard let player, player.isPlaying else { return nil }
        player.updateMeters()
        return player.averagePower(forChannel: 0)
    }

    func stopPlaying() {
        player?.stop()
        player = nil
    }
}
