import Testing

@testable import BoundedMandate

/// The cap is the one number a user types, and it is the number the engine
/// enforces. Getting it wrong here would defeat the whole reason the field
/// exists — a bound off by a factor of a hundred is not a smaller bug than a
/// model misreading a sentence, it is the same bug with a different author.
struct AmountTests {
    @Test func wholeRupeesBecomePaise() {
        #expect(paise(from: "2000") == 200_000)
        #expect(paise(from: "2000.00") == 200_000)
        #expect(paise(from: "0.00") == 0)
    }

    @Test func paiseAreNotDropped() {
        #expect(paise(from: "2000.01") == 200_001)
        #expect(paise(from: "2000.99") == 200_099)
    }

    @Test func aSingleDecimalIsTenths() {
        // "2000.5" is fifty paise, not five. Padding, not parsing as written.
        #expect(paise(from: "2000.5") == 200_050)
    }

    @Test func typingFurnitureIsTolerated() {
        #expect(paise(from: "₹2,000.00") == 200_000)
        #expect(paise(from: "  2,50,000  ") == 25_000_000)
    }

    @Test func nonsenseIsRefusedRatherThanGuessed() {
        // Every one of these would be a bound the user did not set.
        for typed in ["", "   ", "abc", "20.001", "1.2.3", "-5", "2,00 0"] {
            #expect(paise(from: typed) == nil, "accepted \(typed)")
        }
    }

    @Test func theFieldOpensOnTheExactFigureBeingEnforced() {
        #expect(typedAmount(200_000) == "2000.00")
        #expect(typedAmount(200_001) == "2000.01")
        #expect(typedAmount(50) == "0.50")
    }

    @Test func whatIsTypedIsWhatComesBack() {
        // The round trip is the property: a figure shown, edited and saved must
        // survive as the same integer number of paise.
        for value in [0, 1, 99, 200_000, 200_001, 10_000_000] {
            #expect(paise(from: typedAmount(value)) == value, "lost \(value)")
        }
    }
}
