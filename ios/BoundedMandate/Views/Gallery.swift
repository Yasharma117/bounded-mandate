import SwiftUI

/// DEV ONLY — every card in every state on one screen. Deleted before submission.
///
/// The payloads are verbatim from live engine runs, so what renders here is what
/// renders in the thread. Iterating against the real thread costs a six-second
/// model round-trip and a ledger reset per state; this costs a reload.
struct Gallery: View {
    @Environment(\.theme) private var theme
    @State private var section = Section(
        rawValue: UserDefaults.standard.string(forKey: "BMGallerySection") ?? "Lists"
    ) ?? .lists

    enum Section: String, CaseIterable, Identifiable {
        case lists = "Lists"
        case verdicts = "Verdicts"
        case shops = "Shops"
        case rules = "Rules"
        var id: String { rawValue }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 22) {
                switch section {
                case .lists: lists
                case .verdicts: verdicts
                case .shops: shops
                case .rules: rules
                }
            }
            .padding(16)
        }
        .background(Backdrop())
        .navigationTitle("Cards")
        .safeAreaBar(edge: .bottom) {
            Picker("Section", selection: $section) {
                ForEach(Section.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
        }
    }

    @ViewBuilder private var lists: some View {
        if !reversed {
            label("the usual basket — comfortably inside the cap")
            ShoppingListCard(list: Fixtures.usual, onRemove: { _ in }, onAdd: {})
        }

        label("over cap — says so while it is still editable")
        ShoppingListCard(list: Fixtures.overCap, onRemove: { _ in }, onAdd: {})

        label("an item this shop does not stock")
        ShoppingListCard(list: Fixtures.withGap, onRemove: { _ in }, onAdd: {})

        label("a one-off, already spent")
        ShoppingListCard(list: Fixtures.short, editable: false)
    }

    /// DEV: -BMReverse YES starts from the bottom of a section, so the tall
    /// variants can be screenshotted without a scroll gesture.
    private var reversed: Bool { UserDefaults.standard.bool(forKey: "BMReverse") }

    @ViewBuilder private var verdicts: some View {
        ForEach(reversed ? Fixtures.decisions.reversed() : Fixtures.decisions, id: \.id) { decision in
            label(decision.paymentID == nil ? decision.verdict.rawValue : "ALLOW · captured")
            DecisionCard(decision: decision)
        }
    }

    @ViewBuilder private var shops: some View {
        label("cheapest is off-policy — the conflict, stated")
        OffersCard(product: "Aashirvaad atta 5kg", offers: Fixtures.attaOffers)

        label("only one shop carries it")
        OffersCard(product: "Smartwatch", offers: Fixtures.soleOffer)
    }

    @ViewBuilder private var rules: some View {
        label("the standing mandate")
        MandateCard(bounds: Fixtures.standing)

        label("a one-time grant — same object, minutes to live")
        MandateCard(
            bounds: Fixtures.oneTime,
            expiresIn: "in 15 minutes",
            title: "Just this once"
        )
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(theme.textMuted)
    }
}

/// Verbatim from live engine responses.
enum Fixtures {
    static func item(_ name: String, _ paise: Int?) -> ListItem {
        ListItem(
            name: name,
            pricePaise: paise,
            category: "groceries",
            url: "/m/instamart/p/\(name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name)"
        )
    }

    static let usualItems: [ListItem] = [
        item("Aashirvaad atta 5kg", 27_500), item("Basmati rice 1kg", 18_500),
        item("Toned milk 1L x2", 7_000), item("Eggs (12)", 9_000),
        item("Filter coffee 500g", 32_500), item("Bananas 1kg", 6_000),
        item("Toor dal 1kg", 17_500), item("Sunflower oil 1L", 15_500),
        item("Onions 2kg", 8_000), item("Brown bread", 5_500),
        item("Curd 400g", 4_000), item("Cow ghee 500ml", 34_000),
    ]

    static let usual = ShoppingList(
        listID: "usual", name: "My usual groceries", merchant: "instamart",
        items: usualItems, totalPaise: 185_000, capPaise: 200_000, unstocked: [],
        everyDays: 4, due: true, schedule: "Every 4 days"
    )

    static let overCap = ShoppingList(
        listID: "usual", name: "My usual groceries", merchant: "instamart",
        items: usualItems + [item("Bluetooth earbuds", 40_000)],
        totalPaise: 225_000, capPaise: 200_000, unstocked: [],
        everyDays: 4, schedule: "Every 4 days"
    )

    static let withGap = ShoppingList(
        listID: "usual", name: "My usual groceries", merchant: "instamart",
        items: Array(usualItems.prefix(4)) + [item("Kombucha 500ml", nil)],
        totalPaise: 62_000, capPaise: 200_000, unstocked: ["Kombucha 500ml"],
        everyDays: 7, schedule: "Every 7 days"
    )

    static let short = ShoppingList(
        listID: "usual", name: "My usual groceries", merchant: "instamart",
        items: Array(usualItems.prefix(3)), totalPaise: 53_000,
        capPaise: 200_000, unstocked: [],
        kind: "once", spent: true, schedule: "Ordered once, done"
    )

    static func offer(
        _ merchant: String, _ paise: Int, _ name: String,
        shopOK: Bool, categoryOK: Bool = true, category: String = "groceries"
    ) -> Offer {
        Offer(
            merchant: merchant, name: name, pricePaise: paise, category: category,
            url: "/m/\(merchant)/p/\(name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name)",
            imageURL: attaPhoto,
            merchantAllowed: shopOK, categoryAllowed: categoryOK
        )
    }

    /// A real captured URL, so the gallery renders the thumbnail rather than
    /// the empty layout — the gallery exists to iterate on what ships.
    static let attaPhoto =
        "https://media-assets.swiggy.com/swiggy/image/upload/w_160,h_160,c_fit"
        + "/NI_CATALOG/IMAGES/CIW/2026/3/9/805a02b1-e08b-4d4b-aa8f-ab05cabb1e37_1780_1.png"

    static let attaOffers = [
        offer("blinkit", 25_900, "Aashirvaad atta 5kg", shopOK: false),
        offer("zepto", 26_800, "Aashirvaad atta 5kg", shopOK: false),
        offer("instamart", 27_500, "Aashirvaad atta 5kg", shopOK: true),
    ]

    /// The shop is allowed; the category is not. The card must say so.
    static let soleOffer = [
        offer(
            "instamart", 1_500_000, "Smartwatch",
            shopOK: true, categoryOK: false, category: "electronics"
        )
    ]

    static let standing = MandateBounds(
        perTxnMaxPaise: 200_000, merchants: ["instamart"], categories: ["groceries"],
        everyDays: 4, ordersPerWindow: 1
    )

    static let oneTime = MandateBounds(
        perTxnMaxPaise: 40_000, merchants: ["instamart"], categories: ["electronics"],
        everyDays: 1, ordersPerWindow: 1
    )

    static func line(
        _ name: String, _ paise: Int, category: String = "groceries",
        offScope: Bool = false, unclassified: Bool = false
    ) -> CartLine {
        CartLine(
            name: name, pricePaise: paise, category: category,
            url: "/m/instamart/p/\(name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name)",
            offScope: offScope, unclassified: unclassified
        )
    }

    static let usualLines: [CartLine] = usualItems.map { line($0.name, $0.pricePaise ?? 0) }

    static let decisions: [Decision] = [
        Decision(
            verdict: .allow, reasonCode: "ok.in_policy", reasons: [],
            cartID: "instamart_cart_1", realTotalPaise: 185_000, claimedTotalPaise: 185_000,
            idempotencyKey: "b9f1e053257bba1a53c01ed9d81d5f6b",
            orderID: "order_TTaACDfd7hLVNp", keyID: "rzp_test", paymentID: nil,
            items: usualLines, merchant: "instamart",
            summary: "Within your rule", settlement: "Order placed, not yet paid"
        ),
        Decision(
            verdict: .clarify, reasonCode: "category.unknown",
            reasons: [.init(code: "category.unknown", title: "Might not be in scope",
                            detail: "Not sure these are in scope: Whey protein 1kg.")],
            cartID: "instamart_cart_2", realTotalPaise: 32_000, claimedTotalPaise: 32_000,
            idempotencyKey: "c1d2e3f4a5b6", orderID: nil, keyID: nil, paymentID: nil,
            items: [line("Whey protein 1kg", 32_000, category: "", unclassified: true)],
            merchant: "instamart",
            summary: "Might not be in scope", settlement: "Nothing was charged"
        ),
        Decision(
            verdict: .escalate, reasonCode: "category.not_allowed+cap.exceeded",
            reasons: [
                .init(code: "category.not_allowed", title: "Not what you allowed",
                      detail: "2 item(s) outside your scope: Bluetooth earbuds, Phone case."),
                .init(code: "cap.exceeded", title: "Over your limit", detail: "₹400 over your cap."),
            ],
            cartID: "instamart_cart_3", realTotalPaise: 240_000, claimedTotalPaise: 240_000,
            idempotencyKey: "d4e5f6a7b8c9", orderID: nil, keyID: nil, paymentID: nil,
            items: usualLines + [
                line("Bluetooth earbuds", 40_000, category: "electronics", offScope: true),
                line("Phone case", 15_000, category: "accessories", offScope: true),
            ],
            merchant: "instamart"
        ),
        Decision(
            verdict: .deny,
            reasonCode:
                "provenance.total_mismatch+category.not_allowed+cap.exceeded+agent.probing",
            reasons: [
                .init(code: "provenance.total_mismatch", title: "The agent misreported the total",
                      detail: "Agent claimed ₹1,000, the real cart is ₹16,850."),
                .init(code: "category.not_allowed", title: "Not what you allowed",
                      detail: "1 item(s) outside your scope: Smartwatch."),
                .init(code: "cap.exceeded", title: "Over your limit", detail: "₹14,850 over your cap."),
                .init(code: "agent.probing", title: "This agent keeps trying",
                      detail: "3 refused attempts in the last hour. This agent may be "
                            + "compromised — nothing runs on its own until you have looked."),
            ],
            cartID: "instamart_cart_4", realTotalPaise: 1_685_000, claimedTotalPaise: 100_000,
            idempotencyKey: "e5f6a7b8c9d0", orderID: nil, keyID: nil, paymentID: nil,
            items: usualLines + [
                line("Smartwatch", 1_500_000, category: "electronics", offScope: true)
            ],
            merchant: "instamart",
            summary: "The agent misreported the total and over your limit",
            settlement: "Nothing was charged"
        ),
        Decision(
            verdict: .allow, reasonCode: "ok.in_policy", reasons: [],
            cartID: "instamart_cart_5", realTotalPaise: 40_000, claimedTotalPaise: 40_000,
            idempotencyKey: "f6a7b8c9d0e1", orderID: "order_TTaBBDfd7hLVNq",
            keyID: "rzp_test", paymentID: "pay_TTMncCDOzWLlpK",
            items: [line("Bluetooth earbuds", 40_000, category: "electronics")],
            merchant: "instamart",
            summary: "Within your rule", settlement: "Paid"
        ),
    ]
}
