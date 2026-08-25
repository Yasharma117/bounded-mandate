import SwiftUI

/// A blue wash behind everything, and the only thing on screen that reacts.
///
/// This is not decoration — it is what makes Liquid Glass work. Glass refracts
/// whatever sits behind it, so over a flat page it renders as very nearly
/// nothing.
///
/// **Nothing here is recomputed per frame.** `MeshGradient` rebuilds its
/// gradient on the CPU every time its points or colours change, and driving
/// that from a `TimelineView` at 30fps starved everything else: the composer
/// morph, measured, went from 450ms to 2,700ms with the drift on and spent the
/// difference frozen. So both meshes are static and rasterised once, and every
/// moving part is a transform or an opacity — work the GPU does for free.
struct Backdrop: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// 0 in the thread, 1 in voice mode. Cross-fades rather than cutting.
    var voiceness: Double = 0
    /// Live audio level, 0…1. Already smoothed by the session.
    var level: Double = 0

    @State private var drift = false

    var body: some View {
        ZStack {
            mesh(calm).opacity(1 - voiceness * 0.85)
            mesh(vivid).opacity(voiceness)
            breath
        }
        .background(theme.bgSubtle)
        .ignoresSafeArea()
        .onAppear {
            guard !reduceMotion else { return }
            // One transform, driven by the animation system rather than by a
            // per-frame rebuild. Slow enough to be felt and not watched.
            withAnimation(.easeInOut(duration: 26).repeatForever(autoreverses: true)) {
                drift = true
            }
        }
    }

    /// Static points, static colours, rasterised once.
    ///
    /// The scale is not decoration. Rotating a 402x874 rect by θ pulls its
    /// corners inside the screen, and the page shows through as white unless
    /// the layer is oversized enough to cover:
    ///
    ///     scale >= max((W·cosθ + H·sinθ)/W, (H·cosθ + W·sinθ)/H)
    ///
    /// which is 1.186 at 5° and 1.075 at 2°. The first version rotated 5° at a
    /// scale of 1.04, so white corners swung into view on every cycle. Two
    /// degrees reads as drift without needing a quarter of the layer wasted
    /// off-screen, and 1.14 leaves room to spare at both ends of the cycle.
    private func mesh(_ colors: [Color]) -> some View {
        MeshGradient(width: 3, height: 3, points: Self.points, colors: colors)
            .drawingGroup()
            .scaleEffect(reduceMotion ? 1 : (drift ? 1.22 : 1.14))
            .rotationEffect(.degrees(reduceMotion ? 0 : (drift ? 2 : -2)))
    }

    /// The reactive part: a soft highlight that swells with the voice. One
    /// gradient, scaled and faded — no mesh involved.
    private var breath: some View {
        RadialGradient(
            colors: [theme.primary.opacity(0.5), .clear],
            center: .init(x: 0.5, y: 0.78),
            startRadius: 0,
            endRadius: 420
        )
        .scaleEffect(reduceMotion ? 1 : 0.7 + level * 0.9)
        .opacity(reduceMotion ? 0 : voiceness * (0.18 + level * 0.65))
        .blendMode(.plusLighter)
        .allowsHitTesting(false)
    }

    private static let points: [SIMD2<Float>] = [
        SIMD2(0, 0), SIMD2(0.5, 0), SIMD2(1, 0),
        SIMD2(0, 0.45), SIMD2(0.58, 0.52), SIMD2(1, 0.55),
        SIMD2(0, 1), SIMD2(0.45, 1), SIMD2(1, 1),
    ]

    private var calm: [Color] {
        let blue = theme.primary
        let page = theme.bgSubtle
        return [
            blue.opacity(0.34), blue.opacity(0.16), page,
            blue.opacity(0.12), page, blue.opacity(0.10),
            page, blue.opacity(0.14), blue.opacity(0.22),
        ]
    }

    private var vivid: [Color] {
        let blue = theme.primary
        return [
            blue.opacity(0.78), blue.opacity(0.58), blue.opacity(0.44),
            blue.opacity(0.52), blue.opacity(0.40), blue.opacity(0.50),
            blue.opacity(0.46), blue.opacity(0.60), blue.opacity(0.72),
        ]
    }
}
