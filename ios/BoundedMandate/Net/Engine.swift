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
    /// `history` is what was *said* — the user's words and the agent's replies.
    /// Without it every turn arrived with no idea what the last one was, so
    /// "make it Blinkit instead" landed as a sentence about nothing.
    static func runAgent(
        _ text: String, history: [[String: String]] = [], adversarial: Bool = false
    ) async throws -> AgentTurn {
        try await post(
            "/api/agent",
            body: ["text": text, "history": history, "adversarial": adversarial]
        )
    }

    /// Approve one basket the standing rule refused.
    ///
    /// The app sends a cart id and nothing else — no cap, no category, no
    /// address. Every bound comes back derived from the cart the engine itself
    /// fetched, so a compromised client can pick *which* basket to put in front
    /// of the user and cannot touch what approving it would mean.
    static func grantOneTime(cartID: String) async throws -> GrantResponse {
        try await post("/api/mandate/one-time", body: ["cart_id": cartID])
    }

    /// The checkout the server minted, as an absolute URL for Safari.
    static func url(forPath path: String) -> URL? {
        URL(string: baseURL.absoluteString + path)
    }

    /// Fire the scheduler once, by hand — what the timer does, on demand.
    static func runDue() async throws {
        struct Ran: Decodable { let ran: [JSONNull] }
        struct JSONNull: Decodable { init(from decoder: any Decoder) throws {} }
        let _: Ran = try await send("/api/lists/run-due", method: "POST")
    }

    /// The audit trail, and whether the chain still verifies.
    /// What the engine has done, aggregated off the same entries the chain
    /// covers. A pure read — asking does not change the trail.
    static func readStats() async throws -> Stats {
        try await send("/api/stats", method: "GET")
    }

    static func readLedger() async throws -> LedgerPage {
        try await send("/api/ledger", method: "GET")
    }

    /// Replace a list, with categories the user assigned.
    static func writeList(
        _ listID: String, items: [String], categories: [String: String]
    ) async throws -> ShoppingList {
        try await send(
            "/api/list/\(listID)", method: "PUT",
            body: ["item_names": items, "categories": categories]
        )
    }

    /// One product in full, and the alternatives to it.
    static func product(_ name: String, merchant: String) async throws -> ProductDetail {
        let encode = { (s: String) in
            s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? s
        }
        return try await send(
            "/api/product?name=\(encode(name))&merchant=\(encode(merchant))", method: "GET"
        )
    }

    /// Where do I stand. One call, because *which* state the engine is in is
    /// the engine's judgement — the app renders it and does not decide it.
    static func readHome() async throws -> Home {
        try await send("/api/home", method: "GET")
    }

    /// Put down what the home card is showing. Appended to the ledger rather
    /// than mutating anything: "the user looked at it" is an event.
    static func markSeen(_ idempotencyKey: String) async throws {
        struct Ack: Decodable { let state: String }
        let _: Ack = try await send(
            "/api/home/seen", method: "POST", body: ["idempotency_key": idempotencyKey]
        )
    }

    /// Every address on the user's account, and which one orders go to.
    static func readAddresses() async throws -> [DeliveryAddress] {
        struct Wrapper: Decodable { let addresses: [DeliveryAddress] }
        let wrapper: Wrapper = try await send("/api/addresses", method: "GET")
        return wrapper.addresses
    }

    /// Deliver here from now on, and authorise it. A user action: there is no
    /// agent tool that reaches this route.
    static func chooseAddress(_ addressID: String) async throws -> [DeliveryAddress] {
        struct Wrapper: Decodable { let addresses: [DeliveryAddress] }
        let wrapper: Wrapper = try await send(
            "/api/address", method: "PUT", body: ["address_id": addressID]
        )
        return wrapper.addresses
    }

    /// Every list the user keeps, soonest-due first.
    static func readLists() async throws -> [ShoppingList] {
        struct Wrapper: Decodable { let lists: [ShoppingList] }
        let wrapper: Wrapper = try await send("/api/lists", method: "GET")
        return wrapper.lists
    }

    static func createList(
        name: String, items: [String], once: Bool, everyDays: Int?, runOn: String?
    ) async throws -> ShoppingList {
        var body: [String: Any] = [
            "name": name, "item_names": items, "kind": once ? "once" : "standing",
        ]
        if let everyDays { body["every_days"] = everyDays }
        if let runOn { body["run_on"] = runOn }
        return try await send("/api/lists", method: "POST", body: body)
    }

    static func deleteList(_ listID: String) async throws {
        struct Gone: Decodable { let deleted: String }
        let _: Gone = try await send("/api/list/\(listID)", method: "DELETE")
    }

    /// Change *when*, without restating *what*.
    static func setSchedule(
        _ listID: String, everyDays: Int? = nil, paused: Bool? = nil
    ) async throws -> ShoppingList {
        var body: [String: Any] = [:]
        if let everyDays { body["every_days"] = everyDays }
        if let paused { body["paused"] = paused }
        return try await send("/api/list/\(listID)/schedule", method: "PUT", body: body)
    }

    /// The user's list. Read freely; written only by the user.
    static func readList(_ listID: String = "usual") async throws -> ShoppingList {
        try await send("/api/list/\(listID)", method: "GET")
    }

    /// Replace the list. There is no agent path to this call — see `basket.py`.
    static func writeList(_ listID: String, items: [String]) async throws -> ShoppingList {
        try await send("/api/list/\(listID)", method: "PUT", body: ["item_names": items])
    }

    /// Every shop's price for a product, and separately whether the shop and
    /// the category are covered.
    static func catalog(_ query: String) async throws -> [Offer] {
        struct Wrapper: Decodable { let offers: [Offer] }
        let wrapper: Wrapper = try await send(
            "/api/catalog?q=\(query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")",
            method: "GET"
        )
        return wrapper.offers
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

    private static func send<T: Decodable>(
        _ path: String, method: String, body: [String: Any]? = nil
    ) async throws -> T {
        guard let url = URL(string: baseURL.absoluteString + path) else {
            throw Failure("Bad path: \(path)")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw Failure("No response") }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"]
            throw Failure(detail as? String ?? "Engine returned \(http.statusCode)")
        }
        return try decoder.decode(T.self, from: data)
    }
}
