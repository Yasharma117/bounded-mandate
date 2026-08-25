import SwiftUI

/// Razorpay's colour tokens, taken from Blade.
///
/// These are not approximations. Every value is a Blade global scale step
/// resolved through Blade's own semantic mapping in
/// `packages/blade/src/tokens/theme/bladeTheme.ts` — `surface.background.gray.*`,
/// `surface.text.gray.*`, the `interactive` primary, and the four `feedback`
/// hues. The Blade scale each one came from is named in the comment so this
/// stays diffable against upstream.
enum Token {
    // Light — blueGrayLight, azure[500], feedback intense
    private static let lightPalette = Palette(
        bgSubtle: Color(hex: 0xF7F7F7),      // blueGrayLight[100]
        bgIntense: Color(hex: 0xFFFFFF),     // blueGrayLight[0]
        borderSubtle: Color(hex: 0xDEE1E3),  // blueGrayLight[200]
        textNormal: Color(hex: 0x050505),    // blueGrayLight[1300]
        textSubtle: Color(hex: 0x292F32),    // blueGrayLight[1100]
        textMuted: Color(hex: 0x616D75),     // blueGrayLight[700]
        primary: Color(hex: 0x1364F1),       // azure[500] — Razorpay blue
        notice: Color(hex: 0xC75300),        // cider[700]
        negative: Color(hex: 0xD01E11),      // crimson[600]
        orchid: Color(hex: 0x6038BC)         // orchid[700]
    )

    // Dark — blueGrayDark, azure[300], feedback intense on dark
    private static let darkPalette = Palette(
        bgSubtle: Color(hex: 0x1B1C1D),      // blueGrayDark[1300]
        bgIntense: Color(hex: 0x1F2224),     // blueGrayDark[1100]
        borderSubtle: Color(hex: 0x3B3E42),  // blueGrayDark[800]
        textNormal: Color(hex: 0xFFFFFF),    // blueGrayDark[0]
        textSubtle: Color(hex: 0xADB0B3),    // blueGrayDark[300]
        textMuted: Color(hex: 0x7F868C),     // blueGrayDark[500]
        primary: Color(hex: 0x75AAFF),       // azure[300]
        notice: Color(hex: 0xFF8942),        // cider[400]
        negative: Color(hex: 0xEE6A63),      // crimson[400]
        orchid: Color(hex: 0xA97AFF)         // orchid[400]
    )

    static func palette(_ scheme: ColorScheme) -> Palette {
        scheme == .dark ? darkPalette : lightPalette
    }

    struct Palette {
        let bgSubtle, bgIntense, borderSubtle: Color
        let textNormal, textSubtle, textMuted: Color
        let primary, notice, negative, orchid: Color

        /// One verdict, one colour, everywhere it appears.
        ///
        /// `allow` is Razorpay blue rather than Blade's `positive` green: the
        /// brand colour is the colour of *authorised*, which gives it a job
        /// instead of spending it on furniture. The other three keep Blade's
        /// feedback semantics.
        func color(for verdict: Verdict) -> Color {
            switch verdict {
            case .allow: primary
            case .clarify: orchid
            case .escalate: notice
            case .deny: negative
            }
        }
    }
}

/// Reads the environment once so views can say `theme.primary`.
private struct PaletteKey: EnvironmentKey {
    static let defaultValue = Token.palette(.light)
}

extension EnvironmentValues {
    var theme: Token.Palette {
        get { self[PaletteKey.self] }
        set { self[PaletteKey.self] = newValue }
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}
