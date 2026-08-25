import SwiftUI

@main
struct BoundedMandateApp: App {
    @Environment(\.colorScheme) private var scheme

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
        Group {
            // DEV ONLY, with Gallery.swift:
            //   xcrun simctl launch booted <bundle> -BMGallery YES
            if UserDefaults.standard.bool(forKey: "BMGallery") {
                NavigationStack { Gallery() }
            } else {
                ThreadView()
            }
        }
        .environment(\.theme, Token.palette(scheme))
        .tint(Token.palette(scheme).primary)
    }
}
