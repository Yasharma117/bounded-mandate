import SwiftUI

/// One product in full, and what else would do.
///
/// Opened by tapping a line anywhere it appears — a list row, or a thumbnail on
/// the home card. Before this, tapping a line opened a bare HTML page in Safari,
/// which answered "what is this" and nothing else.
///
/// The alternatives are the point. On the mock they are the same product at the
/// other shops, which is the scene the mock was built for: Blinkit undercuts
/// Instamart on the staples, so the cheapest row and the allowed row are
/// different rows. On live they are Instamart's own variants — twelve sizes of
/// Amul. Either way each carries the policy's verdict, so choosing a thing the
/// rule does not cover is a decision made knowingly rather than an escalation
/// discovered later.
struct ProductSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss

    let name: String
    let merchant: String
    /// Supplied only when the sheet was opened from a list the user can edit.
    /// Swapping is a user action on a user-owned document — the agent has no
    /// tool that reaches it, which is exactly why it is safe to offer here.
    var onSwap: ((String) -> Void)?

    @State private var detail: ProductDetail?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if let detail {
                        hero(detail.product)
                        if !detail.alternatives.isEmpty {
                            alternatives(detail)
                        }
                    } else if problem == nil {
                        ProgressView().tint(theme.primary)
                            .frame(maxWidth: .infinity).padding(.top, 60)
                    }
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
            .navigationTitle(name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            detail = try await Engine.product(name, merchant: merchant)
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    // MARK: - the product itself

    private func hero(_ product: Product) -> some View {
        Card(tint: product.buyable ? nil : theme.notice) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top, spacing: 14) {
                    ProductThumb(url: product.imageURL, side: 88)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(product.name)
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundStyle(theme.textNormal)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(rupees(product.pricePaise))
                            .font(.system(size: 26, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(theme.textNormal)
                        Text("\(product.merchant) · \(product.category)")
                            .font(.system(size: 13))
                            .foregroundStyle(theme.textMuted)
                    }
                    Spacer(minLength: 0)
                }
                .padding(16)

                Divider().overlay(theme.borderSubtle)

                HStack(spacing: 7) {
                    Image(systemName: product.buyable ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(product.buyable ? theme.primary : theme.notice)
                    Text(product.blockedReason ?? "Inside your rule — the agent can buy this on its own.")
                        .font(.system(size: 13))
                        .foregroundStyle(product.buyable ? theme.textSubtle : theme.notice)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(16)
            }
        }
    }

    // MARK: - what else would do

    private func alternatives(_ detail: ProductDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(
                text: detail.comparable ? "The same thing elsewhere" : "Other sizes and kinds",
                color: theme.textMuted
            )
            Card {
                VStack(spacing: 0) {
                    ForEach(Array(detail.alternatives.enumerated()), id: \.element.id) { i, alt in
                        if i > 0 { Divider().overlay(theme.borderSubtle.opacity(0.5)) }
                        row(alt, cheaperThan: detail.product)
                    }
                }
            }
            Text(
                onSwap == nil
                    ? "Prices and scope as the engine sees them."
                    : "Choosing one puts it on your list. Only you can do that — "
                        + "the agent has no way to change what your list means."
            )
            .font(.system(size: 12))
            .foregroundStyle(theme.textMuted)
            .padding(.horizontal, 4)
        }
    }

    private func row(_ alt: Product, cheaperThan product: Product) -> some View {
        Button {
            guard let onSwap else { return }
            onSwap(alt.name)
            dismiss()
        } label: {
            HStack(spacing: 12) {
                ProductThumb(url: alt.imageURL, side: 44)
                VStack(alignment: .leading, spacing: 3) {
                    Text(alt.name)
                        .font(.system(size: 14))
                        .foregroundStyle(theme.textNormal)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    if let why = alt.blockedReason {
                        Text(why)
                            .font(.system(size: 11))
                            .foregroundStyle(theme.notice)
                            .lineLimit(1)
                    } else if alt.pricePaise < product.pricePaise {
                        Text("saves \(rupees(product.pricePaise - alt.pricePaise))")
                            .font(.system(size: 11))
                            .foregroundStyle(theme.primary)
                    }
                }
                Spacer(minLength: 8)
                Text(rupees(alt.pricePaise))
                    .font(.system(size: 14, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(alt.buyable ? theme.textNormal : theme.textMuted)
                if onSwap != nil {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(theme.textMuted)
                }
            }
            .padding(.horizontal, 16)
            .frame(minHeight: 62)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
        .disabled(onSwap == nil)
    }
}
