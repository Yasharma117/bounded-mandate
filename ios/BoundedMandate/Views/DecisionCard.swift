import SwiftUI

/// Money renders as a card inside the thread, never as a separate screen — the
/// card is punctuation at the end of a turn.
///
/// Every verdict uses this one view. A receipt and a refusal are the same
/// object wearing different colours, which is the honest shape: the engine ran
/// the same checks either way, and the reader should be able to parse both the
/// same way.
struct DecisionCard: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    /// Set when the checkout redirected back through `warden://paid`. A prompt
    /// to go and ask the engine, never the answer itself.
    @Environment(\.paidGrantID) private var paidGrantID
    let decision: Decision
    /// What the approval and the payment say back into the conversation.
    ///
    /// The card is where a refusal gets answered, and until this existed that
    /// answer never reached the thread — so the agent's next turn was handed a
    /// history ending on its own refusal and rebuilt the basket. Keyed by grant
    /// id so a re-check that lands twice does not say it twice.
    var note: (String, String) -> Void = { _, _ in }

    /// What the user approved, once they have. Held here rather than in the
    /// thread because a grant belongs to the refusal that prompted it — it is
    /// the answer to this card, and reads wrong anywhere else.
    @State private var granted: GrantResponse?
    @State private var granting = false
    @State private var refusal: String?

    /// What the engine last said about the grant. The mint response describes
    /// bounds and cannot describe a payment that has not happened, so this is
    /// the only thing that ever reports one.
    @State private var settled: Grant?
    @State private var showingLedger = false
    @State private var recheckTask: Task<Void, Never>?
    @State private var recheckGeneration = 0

    /// The cart opens by itself when something in it is the reason for the
    /// verdict — that is the moment the reader needs to see the lines, and
    /// making them tap for it would withhold the answer to the question the
    /// card just raised.
    @State private var showingCart: Bool?
    private var cartOpen: Bool { showingCart ?? !decision.flagged.isEmpty }

    private var tint: Color { theme.color(for: decision.verdict) }

    /// The payment, if the engine has confirmed one for this card's grant.
    private var receipt: Grant? {
        guard let settled, settled.paid, settled.grantID == granted?.grant.grantID else {
            return nil
        }
        return settled
    }

    var body: some View {
        VStack(spacing: 12) {
            verdictCard
            if let receipt {
                // Replaces the approval rather than joining it. A countdown to
                // an expiry, beside a payment that already happened, is the
                // same contradiction the stale card had — in a new place.
                paidCard(receipt).arrives(reduceMotion)
            } else if let granted {
                MandateCard(
                    bounds: granted.grant.bounds,
                    expiresIn: granted.grant.expiresIn,
                    title: "One-time approval"
                )
                .arrives(reduceMotion)
                if let checkout { reopen(checkout) }
            }
        }
        .sheet(isPresented: $showingLedger) { LedgerSheet() }
        // Two ways in, and both only ask a question.
        //
        // The redirect is the fast one: Safari hands back the instant the
        // payment clears. Coming to the foreground is the one that works when
        // it does not — the scheme missing, the tab left open, the user
        // switching back by hand — and the app is backgrounded for the whole
        // payment either way, so `.active` is exactly when the answer changed.
        .onChange(of: paidGrantID) { _, _ in beginRecheck() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { beginRecheck() }
        }
        .onDisappear { recheckTask?.cancel() }
    }

    /// Ask the engine what became of the approval.
    ///
    /// Nothing here trusts the URL that woke it: a `warden://paid` link can be
    /// opened by anything, and only the engine has seen a signed callback. A
    /// failure is silent on purpose — the card is already showing something
    /// true, and "could not re-check" is noise beside it.
    private func beginRecheck() {
        recheckGeneration += 1
        let generation = recheckGeneration
        recheckTask?.cancel()
        recheckTask = Task { await recheck(generation: generation) }
    }

    private func recheck(generation: Int) async {
        guard let id = granted?.grant.grantID, receipt == nil else { return }
        guard let fresh = try? await Engine.readGrant(id) else { return }
        guard !Task.isCancelled, generation == recheckGeneration else { return }
        withAnimation(Motion.respectful(Motion.enter(), reduced: reduceMotion)) {
            settled = fresh
        }
        // The one turn that ends the subject. Without it the conversation still
        // reads as an open refusal, and asking "did that go through?" gets the
        // basket built a second time.
        if fresh.paid {
            note("paid-\(id)", "That one is paid — \(paidHeadline(fresh)) Nothing else is owed on it.")
        }
    }

    private var checkout: URL? {
        granted?.payPath.flatMap(Engine.url(forPath:))
    }

    private var verdictCard: some View {
        Card(tint: tint) {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 7) {
                        Image(systemName: decision.verdict.symbol)
                            .font(.system(size: 13))
                            .foregroundStyle(tint)
                        Eyebrow(text: decision.verdict.headline, color: tint)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(rupees(decision.realTotalPaise))
                            .font(.system(size: 34, weight: .bold))
                            .monospacedDigit()
                            .kerning(-0.8)
                            .foregroundStyle(theme.textNormal)
                            .textSelection(.enabled)

                        if decision.lied {
                            Text("the agent reported \(rupees(decision.claimedTotalPaise))")
                                .font(.system(size: 13))
                                .foregroundStyle(theme.negative)
                        } else if let merchant = decision.merchant {
                            Text(merchant)
                                .font(.system(size: 13))
                                .foregroundStyle(theme.textMuted)
                        }
                    }

                    if !decision.reasons.isEmpty {
                        VStack(alignment: .leading, spacing: 13) {
                            ForEach(decision.reasons, id: \.self) { reason in
                                ReasonRow(reason: reason, tint: tint)
                            }
                        }
                    }
                }
                .padding(18)

                if !decision.items.isEmpty {
                    Divider().overlay(theme.borderSubtle)
                    cart
                }

                Divider().overlay(theme.borderSubtle)

                VStack(spacing: 9) {
                    // Words, not codes. The reference is worth showing because
                    // it is something the user could quote back to support; the
                    // reason string is not.
                    // `decision.settlement` was captured when the proposal was
                    // ruled on and says "Nothing was charged" forever. That was
                    // true then and is not true now. The ledger entry behind it
                    // is untouched — what changes is which of two true things
                    // this row shows.
                    DetailRow(
                        label: "Outcome",
                        value: receipt == nil ? decision.settlement : "Paid",
                        color: receipt == nil ? tint : theme.primary
                    )
                    if let reference = decision.paymentID ?? decision.orderID {
                        DetailRow(label: "Reference", value: reference, mono: true)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)

                if decision.grantable && granted == nil {
                    Divider().overlay(theme.borderSubtle)
                    approve
                }
            }
        }
    }

    /// The way out of a refusal that is *not* raising the rule.
    ///
    /// The button sends a cart id and nothing else. Everything it comes back
    /// with — the cap, the shop, the address, the fifteen minutes — was derived
    /// server-side from the basket the engine fetched, so what this affords is
    /// the choice to approve, never the terms of it.
    ///
    /// **Filled, and the widest thing on the card.** It used to be a tinted
    /// row under a divider, which is the same shape this card uses for "Verify
    /// the chain" and for the cart disclosure — both of them rows you read
    /// rather than the one control that decides whether anything gets bought.
    /// So it read as another section of the card and went untapped, and a
    /// refusal with an invisible way out is a refusal with no way out.
    @ViewBuilder private var approve: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Says what approving actually does, because the card above it has
            // just spent four lines saying why this basket is not allowed. It
            // is allowed if you say so — that is the whole point of the button,
            // and it should not be something you have to infer.
            Text("Out of scope is not the same as refused. Approve it and this "
                 + "one basket goes through, once, at this price.")
                .font(.system(size: 13))
                .foregroundStyle(theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            Button {
                Task { await mintGrant() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: granting ? "hourglass" : "checkmark.shield.fill")
                        .font(.system(size: 15, weight: .semibold))
                    // The same words `wording.py` gives the home card for this
                    // action. One product, one name for approving a basket.
                    Text(granting ? "Approving…" : "Approve just this basket")
                        .font(.system(size: 16, weight: .semibold))
                    Text("·").opacity(0.5)
                    Text(rupees(decision.realTotalPaise))
                        .font(.system(size: 16, weight: .semibold))
                        .monospacedDigit()
                }
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(theme.orchid, in: .capsule)
                .contentShape(.capsule)
            }
            .buttonStyle(.pressable)
            .disabled(granting)
            .opacity(granting ? 0.6 : 1)
            .accessibilityLabel(
                "Approve just this basket for \(rupees(decision.realTotalPaise))"
            )

            if let refusal {
                Text(refusal)
                    .font(.system(size: 13))
                    .foregroundStyle(theme.negative)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 14)
        .padding(.bottom, 16)
    }

    /// What the approval turned into.
    ///
    /// Says the same things the home card's own `paid` state says, in the same
    /// order, because they are describing one event and a reader who sees both
    /// should not have to reconcile them.
    private func paidCard(_ grant: Grant) -> some View {
        Card(tint: theme.primary) {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 7) {
                        Image(systemName: "checkmark.seal.fill")
                            .font(.system(size: 13))
                            .foregroundStyle(theme.primary)
                        Eyebrow(text: "Paid", color: theme.primary)
                    }
                    Text(paidHeadline(grant))
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(theme.textNormal)
                        .fixedSize(horizontal: false, vertical: true)
                    if let reference = grant.paymentID {
                        Text(reference)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(theme.textMuted)
                            .textSelection(.enabled)
                    }
                }
                .padding(18)

                Divider().overlay(theme.borderSubtle)

                Button { showingLedger = true } label: {
                    HStack(spacing: 7) {
                        Image(systemName: "checkmark.seal")
                        Text("Verify the chain")
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(theme.primary)
                    .padding(.horizontal, 18)
                    .frame(minHeight: 48)
                    .contentShape(.rect)
                }
                .buttonStyle(.pressable)
            }
        }
    }

    /// "₹272 at instamart. It is on its way."
    ///
    /// The amount comes from the grant the engine minted off the cart it
    /// fetched itself, so it is the figure that was authorised rather than one
    /// the client added up.
    private func paidHeadline(_ grant: Grant) -> String {
        let amount = grant.amountPaise.map(rupees) ?? "Your order"
        let where_ = grant.merchant.map { " at \($0)" } ?? ""
        return "\(amount)\(where_). It is on its way."
    }

    private func reopen(_ url: URL) -> some View {
        Link(destination: url) {
            HStack(spacing: 7) {
                Image(systemName: "arrow.up.right.square")
                Text("Open the checkout")
            }
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(theme.orchid)
            .frame(maxWidth: .infinity, minHeight: 48)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
    }

    private func mintGrant() async {
        granting = true
        refusal = nil
        defer { granting = false }
        do {
            let response = try await Engine.grantOneTime(cartID: decision.cartID)
            withAnimation(Motion.respectful(Motion.enter(), reduced: reduceMotion)) {
                granted = response
            }
            note(
                "grant-\(response.grant.grantID)",
                "You approved that basket — \(rupees(decision.realTotalPaise)), once. "
                    + "Taking you to the checkout."
            )
            // The user just approved it; making them tap a second time to reach
            // the checkout is friction with nothing behind it. The card stays
            // as the record of what was approved.
            if let checkout { openURL(checkout) }
        } catch {
            refusal = error.localizedDescription
        }
    }

    @ViewBuilder private var cart: some View {
        Button {
            withAnimation(Motion.respectful(Motion.move(), reduced: reduceMotion)) { showingCart = !cartOpen }
        } label: {
            HStack(spacing: 6) {
                Text(
                    decision.flagged.isEmpty
                        ? plural(decision.items.count, "item")
                        : "\(decision.flagged.count) of \(plural(decision.items.count, "item")) flagged"
                )
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(decision.flagged.isEmpty ? theme.textMuted : tint)
                Image(systemName: "chevron.down")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(theme.textMuted)
                    .rotationEffect(.degrees(cartOpen ? 180 : 0))
                Spacer()
            }
            .padding(.horizontal, 18)
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)

        if cartOpen {
            VStack(spacing: 0) {
                ForEach(decision.orderedItems) { line in
                    HStack(spacing: 10) {
                        ProductThumb(url: line.imageURL)
                        Text(line.name)
                            .font(.system(size: 14))
                            .foregroundStyle(line.flagged ? theme.textNormal : theme.textSubtle)
                            .lineLimit(2)
                        if let note = line.note {
                            Text(note)
                                .font(.system(size: 11, weight: .medium))
                                .foregroundStyle(tint)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(tint.opacity(0.14), in: .capsule)
                        }
                        Spacer(minLength: 8)
                        Text(rupees(line.pricePaise))
                            .font(.system(size: 14))
                            .monospacedDigit()
                            .foregroundStyle(line.flagged ? theme.textNormal : theme.textMuted)
                    }
                    .padding(.horizontal, 18)
                    .padding(.vertical, 6)
                }
            }
            .padding(.bottom, 8)
        }
    }
}
