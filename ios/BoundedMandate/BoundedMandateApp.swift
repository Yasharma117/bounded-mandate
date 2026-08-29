import SwiftUI

@main
struct BoundedMandateApp: App {
    var body: some Scene {
        WindowGroup {
            Root()
        }
    }
}

/// Resolves the Blade palette once and hands it down, so no view reaches for
/// a raw colour.
private struct Root: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HomeView()
            .environment(\.theme, Token.palette(scheme))
            .tint(Token.palette(scheme).primary)
    }
}
