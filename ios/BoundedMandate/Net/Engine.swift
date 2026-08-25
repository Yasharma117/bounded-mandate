import Foundation

/// The engine lives behind HTTP. This app proposes and renders verdicts; it
/// holds no policy, no Razorpay key and no ElevenLabs key.
///
/// Decompiling this bundle yields nothing, because there is nothing in it.
enum Engine {
    /// Override with `-BMEngineHost http://…` in the scheme, or edit here.
    static let baseURL: URL = {
        if let override = UserDefaults.standard.string(forKey: "BMEngineHost"),
           let url = URL(string: override) {
            return url
        }
        return URL(string: "http://127.0.0.1:8117")!
    }()

    struct Failure: LocalizedError {
        let errorDescription: String?
        init(_ message: String) { errorDescription = message }
    }

    private static let decoder = JSONDecoder()

    /// Hand an instruction to the buyer agent and report what it did.
    ///
    /// `adversarial` swaps in an agent working against the account holder. It
    /// changes what the agent *tries*, never what the engine permits, which is
    /// the only reason it is safe to ship a button for it.
    static func runAgent(_ text: String, adversarial: Bool = false) async throws -> AgentTurn {
        try await post("/api/agent", body: ["text": text, "adversarial": adversarial])
    }

    private static func post<T: Decodable>(_ path: String, body: [String: Any]) async throws -> T {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 60

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw Failure("No response") }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"]
            throw Failure(detail as? String ?? "Engine returned \(http.statusCode)")
        }
        return try decoder.decode(T.self, from: data)
    }
}
