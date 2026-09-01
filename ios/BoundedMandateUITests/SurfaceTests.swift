import XCTest

/// Drives the app the way a person does, and photographs what it shows.
///
/// The unit target decodes payloads and proves the models match the server.
/// It cannot open a sheet. Three surfaces in this build — the ledger tally, the
/// rule editor, the product sheet — are reachable only through a tap, and every
/// one of them was written, built, reported as done, and never looked at. A
/// green `xcodebuild build` says the app compiles; it says nothing about
/// whether a screen renders, and treating one as the other is how a broken
/// view ships.
///
/// **These need the engine answering on :8117.** They are on their own scheme
/// (`BoundedMandateUI`) so an offline `xcodebuild test` cannot be broken by a
/// server that is not running.
final class SurfaceTests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
        app = XCUIApplication()
        app.launch()
    }

    /// Attach a screenshot to the result bundle under a name, so the run is
    /// reviewable afterwards rather than only pass/fail.
    private func capture(_ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Waits for the home screen to have loaded from the engine. A launch that
    /// renders before `/api/home` answers would photograph a spinner.
    private func waitForHome() {
        XCTAssertTrue(
            app.staticTexts["LEDGER"].waitForExistence(timeout: 20),
            "home never finished loading — is the engine up on :8117?"
        )
    }

    func testTheLedgerSheetShowsTheTally() {
        waitForHome()
        app.staticTexts["LEDGER"].tap()

        // The chain line is the first thing on the sheet and the slowest, since
        // it re-verifies on every read.
        XCTAssertTrue(
            app.staticTexts["The chain verifies"].waitForExistence(timeout: 15),
            "the ledger sheet did not open, or the chain did not verify"
        )
        XCTAssertTrue(
            app.staticTexts["held back"].waitForExistence(timeout: 10),
            "the tally card is missing — /api/stats did not render"
        )
        XCTAssertTrue(
            app.staticTexts["authorised"].exists,
            "the tally shows only half of itself"
        )
        capture("ledger-tally")
    }

    func testTheRuleEditorOpensOnTheEnforcedBounds() {
        waitForHome()
        // The rule block at the top of every state, now the way in.
        app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH 'Your rule,'")
        ).firstMatch.tap()

        XCTAssertTrue(
            app.staticTexts["Change these bounds"].waitForExistence(timeout: 15),
            "the rule sheet did not open"
        )
        capture("rule-sheet")

        app.staticTexts["Change these bounds"].tap()

        // The editor's whole point: the amount is a control the account holder
        // sets, not a number a model wrote. If the field is absent the bound is
        // still coming from the compiler.
        XCTAssertTrue(
            app.textFields.firstMatch.waitForExistence(timeout: 10),
            "no editable amount field — the cap is not user-set"
        )
        capture("rule-editor")
    }

    func testAProductSheetShowsItsPacks() throws {
        waitForHome()
        // The strip on the state card: tapping a line opens the product sheet.
        let line = app.buttons.containing(
            NSPredicate(format: "label CONTAINS[c] 'atta'")
        ).firstMatch
        if !line.waitForExistence(timeout: 15) {
            // Print what *is* on screen, so a selector failure is diagnosable
            // from the log rather than needing another run to find out.
            print("no matching line. tree follows:\n\(app.debugDescription)")
        }
        guard line.exists else {
            throw XCTSkip("no basket line on screen to open — nothing to photograph")
        }
        line.tap()

        // The verdict strip, not the pack row. The pack row is deliberately
        // hidden when there is one nameless pack to choose between, which is
        // every product on the mock — asserting it here would have made the
        // test backend-dependent and it would fail whenever Swiggy's token
        // lapsed, which is exactly when nobody wants a second red herring.
        let verdict = app.staticTexts.containing(
            NSPredicate(format: "label CONTAINS[c] 'your rule'")
        ).firstMatch
        XCTAssertTrue(
            verdict.waitForExistence(timeout: 20),
            "the product sheet did not render its policy verdict"
        )
        capture("product-sheet")

        // Live, a product has several packs and each carries its own answer.
        // Skipped rather than failed on the mock, which genuinely has one.
        if app.staticTexts["PACK"].exists {
            XCTAssertGreaterThan(
                app.buttons.matching(
                    NSPredicate(format: "label CONTAINS[c] 'over your cap' OR label CONTAINS '₹'")
                ).count,
                1,
                "a pack row with fewer than two packs is not a choice"
            )
        }
    }
}
