import SwiftUI

/// Where the standing rule is set.
///
/// The one screen in the app where authority is *created* rather than spent, and
/// the reason it exists as controls rather than a confirmation is the same
/// principle the rest of the product runs on: the model proposes, the account
/// holder decides.
///
/// Speaking a rule still fills these in — that path is unchanged and it is the
/// good part. What changed is that the model's reading is a *draft* in the
/// literal sense. It cannot become a bound without passing through a control
/// somebody touched, so a misread "₹2,000" dies here rather than being enforced
/// faithfully for the rest of the mandate's life.
///
/// The cap is typed in rupees *and paise* on purpose. A field that showed
/// "₹2,000" would hide the difference between 200000 and 2000 paise, which is
/// exactly the confusion the whole screen exists to remove.
struct RuleEditor: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss

    let bounds: RuleBounds
    /// Handed the committed rule, so the caller refreshes from the engine's
    /// answer rather than from what it hoped it sent.
    var onSaved: (RuleBounds) -> Void

    @State private var amount = ""
    @State private var merchants: Set<String> = []
    @State private var categories: Set<String> = []
    @State private var everyDays = 4
    @State private var saving = false
    @State private var problem: String?
    @FocusState private var editingAmount: Bool

    private var capPaise: Int? { paise(from: amount) }

    /// Every reason this rule cannot be committed, in the order they are read.
    /// Shown as one line rather than three, because a form that lights up
    /// everywhere at once teaches people to ignore it.
    private var blocker: String? {
        guard let capPaise else { return "Enter a cap, in rupees and paise." }
        if capPaise <= 0 { return "A cap has to be more than nothing." }
        if capPaise > bounds.maxCapPaise {
            return "That is over \(rupees(bounds.maxCapPaise)) — check for a stray zero."
        }
        if merchants.isEmpty { return "Pick at least one shop." }
        if categories.isEmpty { return "Pick at least one thing it may buy." }
        return nil
    }

    private var changed: Bool {
        capPaise != bounds.perTxnMaxPaise
            || merchants != Set(bounds.merchants)
            || categories != Set(bounds.categories)
            || everyDays != bounds.everyDays
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    cap
                    picker(
                        "Shops it may buy from", bounds.merchantOptions,
                        chosen: $merchants
                    )
                    picker(
                        "What it may buy", bounds.categoryOptions,
                        chosen: $categories
                    )
                    cadence
                    footnote
                    if let problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                    }
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("Set your rule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(blocker != nil || !changed || saving)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if let blocker, changed || capPaise == nil {
                    Text(blocker)
                        .font(.system(size: 12))
                        .foregroundStyle(theme.notice)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 12)
                        .background(.bar)
                }
            }
        }
        .onAppear {
            amount = typedAmount(bounds.perTxnMaxPaise)
            merchants = Set(bounds.merchants)
            categories = Set(bounds.categories)
            everyDays = bounds.everyDays
        }
    }

    // MARK: - the number that matters

    private var cap: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                Eyebrow(text: "Most it may spend per order", color: theme.textMuted)
                HStack(spacing: 6) {
                    Text("₹")
                        .font(.system(size: 30, weight: .semibold))
                        .foregroundStyle(theme.textMuted)
                    TextField("0.00", text: $amount)
                        .font(.system(size: 34, weight: .semibold))
                        .monospacedDigit()
                        .foregroundStyle(theme.textNormal)
                        .keyboardType(.decimalPad)
                        .focused($editingAmount)
                        .textFieldStyle(.plain)
                }
                Text(
                    editingAmount || capPaise == nil
                        ? "Rupees and paise — 2000.00 is two thousand rupees."
                        : "You set this. Nothing reads it out of a sentence."
                )
                .font(.system(size: 12))
                .foregroundStyle(theme.textMuted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
        }
    }

    // MARK: - the bounds that are names rather than numbers

    private func picker(
        _ title: String, _ options: [String], chosen: Binding<Set<String>>
    ) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                Eyebrow(text: title, color: theme.textMuted)
                // Options the engine served, plus anything already on the rule —
                // so a bound that exists is never silently dropped by a screen
                // that did not know how to show it.
                let all = options + chosen.wrappedValue.filter { !options.contains($0) }.sorted()
                FlowRow(spacing: 8) {
                    ForEach(all, id: \.self) { option in
                        chip(option, on: chosen.wrappedValue.contains(option)) {
                            if chosen.wrappedValue.contains(option) {
                                chosen.wrappedValue.remove(option)
                            } else {
                                chosen.wrappedValue.insert(option)
                            }
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
        }
    }

    private func chip(_ label: String, on: Bool, tap: @escaping () -> Void) -> some View {
        Button { withAnimation(Motion.follow) { tap() } } label: {
            HStack(spacing: 5) {
                if on {
                    Image(systemName: "checkmark")
                        .font(.system(size: 10, weight: .bold))
                }
                Text(label)
                    .font(.system(size: 14, weight: on ? .semibold : .regular))
            }
            .foregroundStyle(on ? .white : theme.textSubtle)
            .padding(.horizontal, 13)
            .padding(.vertical, 8)
            .background(on ? theme.primary : theme.bgSubtle, in: .capsule)
            .overlay(Capsule().stroke(on ? .clear : theme.borderSubtle, lineWidth: 1))
        }
        .buttonStyle(.pressable)
        .accessibilityLabel("\(label)\(on ? ", included" : ", not included")")
    }

    private var cadence: some View {
        Card {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Eyebrow(text: "How often", color: theme.textMuted)
                    Text(everyDays == 1 ? "Every day" : "Every \(everyDays) days")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(theme.textNormal)
                    Text("One order per window.")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textMuted)
                }
                Spacer(minLength: 0)
                Stepper("", value: $everyDays, in: 1...365)
                    .labelsHidden()
            }
            .padding(16)
        }
    }

    private var footnote: some View {
        Text(
            "Speaking a rule fills these in; it does not set them. Every bound here "
            + "is one you put here, which is why a model misreading you cannot become "
            + "authority. The agent can read none of it and change none of it."
        )
        .font(.system(size: 12))
        .foregroundStyle(theme.textMuted)
        .padding(.horizontal, 4)
    }

    private func save() async {
        guard let capPaise, blocker == nil else { return }
        saving = true
        defer { saving = false }
        do {
            let committed = try await Engine.setRule(
                capPaise: capPaise,
                merchants: merchants.sorted(),
                categories: categories.sorted(),
                everyDays: everyDays
            )
            onSaved(committed)
            dismiss()
        } catch {
            problem = error.localizedDescription
        }
    }
}

/// Chips that wrap. `LazyVGrid` cannot do it — the columns are fixed width and
/// "groceries" and "personal care" are not.
struct FlowRow: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += line + spacing
                line = 0
            }
            x += size.width + spacing
            line = max(line, size.height)
        }
        return CGSize(width: width, height: y + line)
    }

    func placeSubviews(
        in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
    ) {
        var x = bounds.minX, y = bounds.minY, line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += line + spacing
                line = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            line = max(line, size.height)
        }
    }
}
