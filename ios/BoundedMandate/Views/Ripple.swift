import SwiftUI

/// A ring that leaves the composer when the conversation changes hands.
///
/// Voice has no cursor and no button press to acknowledge. The only way to know
/// the machine noticed you stopped talking is for something to happen — so this
/// fires once per transition: you stopped, it is thinking, it is speaking.
///
/// It is short on purpose. A hand-over happens several times per conversation,
/// and motion seen that often has to stay under the 300ms budget or it becomes
/// something to sit through. The first version swept for a full second, which
/// looked considered once and tedious by the third turn.
struct Ripple: View {
    /// Bumping this fires one ring.
    let trigger: Int
    let color: Color
    /// Where it starts, in unit space. The composer, so it reads as coming
    /// *from* the thing you just spoke into.
    var origin: UnitPoint = .init(x: 0.5, y: 0.88)

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expansion: CGFloat = 0
    @State private var fade: Double = 0

    var body: some View {
        // The observer lives out here, not on the ring: the ring is conditional,
        // and an `onChange` attached to a view that has not been mounted yet
        // never fires — which silently killed this effect entirely.
        Color.clear
            .allowsHitTesting(false)
            .overlay { if fade > 0 { ring } }
            .onChange(of: trigger) { fire() }
    }

    /// Mounted only while a ring is actually travelling. A full-screen
    /// GeometryReader that is always present is a layout pass the thread pays
    /// for on every frame it scrolls.
    private var ring: some View {
        GeometryReader { geometry in
            let widest = max(geometry.size.width, geometry.size.height) * 2.2
            Circle()
                .stroke(color.opacity(fade), lineWidth: 1.5)
                .frame(width: widest * expansion, height: widest * expansion)
                .position(
                    x: geometry.size.width * origin.x,
                    y: geometry.size.height * origin.y
                )
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }

    private func fire() {
        // A ring that travels is movement, and movement is the part reduced
        // motion asks you to drop. The state change still needs marking, so it
        // becomes a still pulse of colour instead of a sweep.
        guard !reduceMotion else {
            fade = 0.28
            withAnimation(Motion.enter(0.24)) { fade = 0 }
            return
        }

        expansion = 0.04
        fade = 0.5
        // Retargets from wherever the last ring got to rather than snapping
        // back to zero, so two fast transitions overlap instead of stuttering.
        withAnimation(Motion.enter(0.28)) {
            expansion = 1
            fade = 0
        }
    }
}
