import SwiftUI

/// A blue-to-white wash behind everything.
///
/// This is not decoration — it is what makes Liquid Glass work. Glass refracts
/// whatever sits behind it, so over a flat page it renders as very nearly
/// nothing. Give it a field with some variation and the cards start to read as
/// material rather than as outlines.
///
/// `MeshGradient` is a real shader, shipped by the platform — no WebGL host and
/// no dependency. In the thread it drifts slowly and stays out of the way. In
/// voice mode it is driven by `intensity`, which is where it earns its keep:
/// the field brightening as you speak is how a screen with no words on it yet
/// still says *I am listening*.
struct Backdrop: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// 0 at rest, 1 at full voice. Drives brightness and how far the mesh moves.
    var intensity: Double = 0
    /// Voice mode leans on colour much harder than a page background should,
    /// and is also the only time this is allowed to move.
    var vivid: Bool = false

    /// A mesh gradient is not cheap, and this sits behind a scrolling thread.
    /// Driving it from a timeline while nobody is talking spends a frame budget
    /// on a background nobody is looking at — and costs it again on every
    /// scroll frame, because the whole thing recomposites.
    private var shouldDrift: Bool { vivid && !reduceMotion }

    var body: some View {
        Group {
            if shouldDrift {
                TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
                    let seconds = timeline.date.timeIntervalSinceReferenceDate
                    MeshGradient(
                        width: 3,
                        height: 3,
                        points: Self.points(at: seconds, intensity: intensity),
                        colors: colors
                    )
                }
            } else {
                // Still. In the thread there is nothing to react to, and for
                // someone who asked for less motion there should be nothing
                // moving at all.
                MeshGradient(
                    width: 3,
                    height: 3,
                    points: Self.points(at: 0, intensity: 0),
                    colors: colors
                )
            }
        }
        .background(theme.bgSubtle)
        .ignoresSafeArea()
    }

    // Broken out and explicitly typed: the compiler cannot check these literals
    // inline in reasonable time.
    private static func points(at seconds: TimeInterval, intensity: Double) -> [SIMD2<Float>] {
        // Louder means faster and wider, so the motion reads as a response
        // rather than as an idle animation that happens to be running.
        let energy = Float(0.06 + intensity * 0.16)
        let rate = 1 + intensity * 2.2
        let drift = Float(sin(seconds * rate / 9)) * energy
        let sway = Float(cos(seconds * rate / 13)) * energy * 0.85
        return [
            SIMD2(0, 0), SIMD2(0.5 + sway, 0), SIMD2(1, 0),
            SIMD2(0, 0.5 - drift), SIMD2(0.5 + drift, 0.5 + sway), SIMD2(1, 0.5 + drift),
            SIMD2(0, 1), SIMD2(0.5 - sway, 1), SIMD2(1, 1),
        ]
    }

    private var colors: [Color] {
        let lift = vivid ? 0.34 + intensity * 0.42 : 0.0
        let blue = theme.primary
        let page = theme.bgSubtle
        func wash(_ base: Double) -> Color { blue.opacity(min(base + lift, 0.92)) }
        return [
            wash(0.34), wash(0.16), vivid ? wash(0.08) : page,
            wash(0.12), vivid ? wash(0.06) : page, wash(0.10),
            vivid ? wash(0.09) : page, wash(0.14), wash(0.22),
        ]
    }
}
