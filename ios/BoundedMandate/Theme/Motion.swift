import SwiftUI

/// The app's motion vocabulary, in one place.
///
/// Curves are the strong custom ones rather than SwiftUI's built-ins, which are
/// too weak to read as deliberate. Durations sit under 300ms for anything that
/// is UI; the two exceptions are named and justified where they are used.
///
/// **Nothing here uses ease-in.** It starts slow, which delays the exact moment
/// the user is watching — an ease-out at 200ms feels faster than an ease-in at
/// 200ms even though they take the same time.
enum Motion {
    /// Entering and exiting. Starts fast, settles — the responsive one.
    ///
    /// `cubic-bezier(0.16, 1, 0.3, 1)`, a harder ease-out than the usual
    /// `0.23, 1, 0.32, 1`. Nearly all the distance is covered in the first
    /// third, which is what makes a transition read as decisive rather than as
    /// something you are waiting through.
    static func enter(_ duration: TimeInterval = 0.22) -> Animation {
        .timingCurve(0.16, 1, 0.3, 1, duration: duration)
    }

    /// Something already on screen moving or changing size.
    /// `cubic-bezier(0.77, 0, 0.175, 1)`
    static func move(_ duration: TimeInterval = 0.2) -> Animation {
        .timingCurve(0.77, 0, 0.175, 1, duration: duration)
    }

    /// A large spatial change — the composer becoming an orb. The iOS drawer
    /// curve, and the one place a longer duration is right: it is a deliberate
    /// press that changes what the whole screen is for.
    /// `cubic-bezier(0.32, 0.72, 0, 1)`
    static let morph = Animation.timingCurve(0.32, 0.72, 0, 1, duration: 0.32)

    /// Coming back is a system response, not a decision, so it snaps.
    /// Asymmetry is the point: deliberate in, quick out.
    static let unmorph = Animation.timingCurve(0.32, 0.72, 0, 1, duration: 0.2)

    /// Continuous input — audio level driving a scale. A spring keeps momentum
    /// where tying a transform straight to the signal reads as artificial.
    static let follow = Animation.spring(duration: 0.22, bounce: 0.08)

    /// How far apart two things in the same turn should land. Enough to read as
    /// a sequence, short enough that nobody waits for it.
    static let stagger: TimeInterval = 0.05

    /// Reduced motion keeps the fade and drops the travel.
    ///
    /// Gentler, not gone: removing the transition entirely makes state changes
    /// jarring, which is the thing motion was there to prevent.
    static func respectful(_ animation: Animation, reduced: Bool) -> Animation {
        reduced ? .easeOut(duration: 0.15) : animation
    }
}

extension View {
    /// The house entrance: rise a little and fade, never from nothing.
    ///
    /// `scale(0)` and pure fades are both wrong — nothing in the real world
    /// appears from nothing, and a fade with no movement gives the eye nowhere
    /// to travel. 0.98 and 8pt is enough to register without being a show.
    func arrives(_ reduced: Bool, delay: TimeInterval = 0) -> some View {
        transition(
            .asymmetric(
                insertion: .modifier(
                    active: Arrival(progress: 0, reduced: reduced),
                    identity: Arrival(progress: 1, reduced: reduced)
                )
                .animation(Motion.enter().delay(delay)),
                // Leaving is quieter than arriving. Exits should not compete
                // for attention with whatever replaced them.
                removal: .opacity.animation(Motion.enter(0.16))
            )
        )
    }
}

private struct Arrival: ViewModifier {
    let progress: Double
    let reduced: Bool

    func body(content: Content) -> some View {
        content
            .opacity(progress)
            .scaleEffect(reduced ? 1 : 0.985 + 0.015 * progress, anchor: .bottom)
            .offset(y: reduced ? 0 : 10 * (1 - progress))
    }
}


/// Press feedback for anything tappable that is not already system-styled.
///
/// 0.97 — subtle enough to read as give rather than as the control shrinking.
/// Nothing below 0.95: past that it stops looking like a press and starts
/// looking like a bug.
struct Pressable: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.86 : 1)
            // Press is a physical event and wants to feel immediate; release
            // can take slightly longer without feeling slow.
            .animation(
                Motion.enter(configuration.isPressed ? 0.09 : 0.16),
                value: configuration.isPressed
            )
    }
}

extension ButtonStyle where Self == Pressable {
    static var pressable: Pressable { Pressable() }
}
