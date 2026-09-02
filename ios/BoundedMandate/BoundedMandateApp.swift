import SwiftUI

@main
struct BoundedMandateApp: App {
    var body: some Scene {
        WindowGroup {
            Root()
        }
    }
}

/// The last grant the checkout said it had paid for.
///
/// Published down the tree rather than passed, because the card that needs to
/// know is several levels inside a thread inside a cover, and it is the only
/// thing that cares. `nil` until a `warden://paid` arrives.
private struct PaidGrantKey: EnvironmentKey {
    static let defaultValue: String? = nil
}

extension EnvironmentValues {
    var paidGrantID: String? {
        get { self[PaidGrantKey.self] }
        set { self[PaidGrantKey.self] = newValue }
    }
}

/// Resolves the Blade palette once and hands it down, so no view reaches for
/// a raw colour.
private struct Root: View {
    @Environment(\.colorScheme) private var scheme
    @State private var paidGrantID: String?

    var body: some View {
        HomeView()
            .environment(\.theme, Token.palette(scheme))
            .environment(\.paidGrantID, paidGrantID)
            .tint(Token.palette(scheme).primary)
            // Razorpay Checkout runs in Safari, so the app is backgrounded for
            // the whole payment and learns nothing on its own. `pay.html`
            // redirects here on success and this is where that lands.
            //
            // It carries the grant id rather than trusting whichever card
            // happens to be on screen — and it is a *hint*, not evidence: the
            // card re-reads the grant from the engine, which knows whether a
            // signed callback actually arrived. Anything can open a URL.
            .onOpenURL { url in
                guard url.scheme == "warden", url.host == "paid" else { return }
                let id = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "grant" }?.value
                paidGrantID = id ?? ""
            }
    }
}
