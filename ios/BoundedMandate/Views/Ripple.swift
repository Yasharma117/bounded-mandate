import SwiftUI

/// A ring that leaves the composer and crosses the screen when the conversation
/// changes hands.
///
/// Voice has no cursor and no button press to acknowledge. The only way to know
/// the machine noticed you stopped talking is for something to happen — one
/// ring per transition, tinted by what it is announcing. It reads from across a
/// desk and costs nothing to ignore.
struct Ripple: View {
    /// Bumping this fires one ring. Phase changes drive it.
    let trigger: Int
    let color: Color
    /// Where it starts, in unit space. The composer, so it reads as coming
    /// *from* the thing you just spoke into.
    var origin: UnitPoint = .init(x: 0.5, y: 0.88)

    @State private var expansion: CGFloat = 0
    @State private var fade: Double = 0

    var body: some View {
        GeometryReader { geometry in
            let widest = max(geometry.size.width, geometry.size.height) * 2.4
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
        .onChange(of: trigger) {
            expansion = 0
            fade = 0.5
            withAnimation(.easeOut(duration: 1.0)) {
                expansion = 1
                fade = 0
            }
        }
    }
}
