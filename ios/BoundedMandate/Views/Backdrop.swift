import SwiftUI

/// A blue-to-white wash behind everything.
///
/// This is not decoration — it is what makes Liquid Glass work. Glass refracts
/// whatever sits behind it, so over a flat page it renders as very nearly
/// nothing. Give it a field with some variation and the cards start to read as
/// material rather than as outlines.
///
/// `MeshGradient` is a real shader, shipped by the platform — no WebGL host and
/// no dependency. It drifts rather than sitting still, because a static field
/// behind moving glass gives the refraction nothing to catch; the motion is far
/// too slow to compete with a money figure for attention.
struct Backdrop: View {
    @Environment(\.theme) private var theme

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20)) { timeline in
            let seconds = timeline.date.timeIntervalSinceReferenceDate
            MeshGradient(
                width: 3,
                height: 3,
                points: Self.points(at: seconds),
                colors: colors
            )
        }
        .background(theme.bgSubtle)
        .ignoresSafeArea()
    }

    // Broken out and explicitly typed: the compiler cannot check these
    // literals inline in reasonable time.
    private static func points(at seconds: TimeInterval) -> [SIMD2<Float>] {
        let drift = Float(sin(seconds / 9)) * 0.06
        let sway = Float(cos(seconds / 13)) * 0.05
        return [
            SIMD2(0, 0), SIMD2(0.5 + sway, 0), SIMD2(1, 0),
            SIMD2(0, 0.5 - drift), SIMD2(0.5 + drift, 0.5 + sway), SIMD2(1, 0.5 + drift),
            SIMD2(0, 1), SIMD2(0.5 - sway, 1), SIMD2(1, 1),
        ]
    }

    private var colors: [Color] {
        let blue = theme.primary
        let page = theme.bgSubtle
        return [
            blue.opacity(0.34), blue.opacity(0.16), page,
            blue.opacity(0.12), page, blue.opacity(0.10),
            page, blue.opacity(0.14), blue.opacity(0.22),
        ]
    }
}
