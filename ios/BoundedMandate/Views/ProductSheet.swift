import SwiftUI

/// One product, its packs, and what else would do.
///
/// Built against Instamart's own product sheet, because that is the layout
/// anybody who shops has already learned: hero image, delivery time and rating,
/// the brand, the name, then a row of pack tiles you choose between.
///
/// One thing sits where Instamart puts a marketing banner, and it is the reason
/// this screen exists: **the packs carry different prices, so they carry
/// different verdicts.** `1 ltr` at ₹77 clears a ₹2,000 rule and `1 ltr x 12`
/// at ₹924 may not. The size selector becomes the place you can see what your
/// own rule reaches — which is the whole product, in one control, on the one
/// screen somebody opened in order to choose.
struct ProductSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss

    let name: String
    let merchant: String
    /// Supplied only when the sheet was opened from a list the user can edit.
    /// Swapping is a user action on a user-owned document — the agent has no
    /// tool that reaches it, which is exactly why it is safe to offer here.
    var onSwap: ((String) -> Void)?

    /// What the sheet is showing *now* — an alternative replaces it in place,
    /// the way tapping a similar product on Instamart does.
    @State private var showing: String?
    @State private var detail: ProductDetail?
    @State private var chosen: String?
    @State private var problem: String?

    private var product: Product? { detail?.product }
    private var pack: Pack? {
        guard let product else { return nil }
        return product.variants.first { $0.skuID == chosen } ?? product.cheapest
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let product {
                        hero(product)
                        verdict(product)
                        heading(product)
                        if product.variants.count > 1 || product.variants.first?.label.isEmpty == false {
                            packs(product)
                        }
                        if let detail, !detail.alternatives.isEmpty {
                            rail(detail)
                        }
                    } else if problem == nil {
                        ProgressView().tint(theme.primary)
                            .frame(maxWidth: .infinity).padding(.top, 80)
                    }
                    if let problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                            .padding(.horizontal, 16)
                    }
                }
                .padding(.vertical, 12)
            }
            .background(Backdrop())
            .navigationTitle(showing ?? name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom) { if pack != nil { bar } }
        }
        .task(id: showing ?? name) { await load(showing ?? name) }
    }

    private func load(_ what: String) async {
        do {
            let got = try await Engine.product(what, merchant: merchant)
            detail = got
            // The cheapest pack that your rule actually reaches, so the sheet
            // opens on something you can act on rather than on something it
            // then has to refuse.
            chosen = (got.product.variants.first { $0.withinCap && $0.inStock }
                ?? got.product.cheapest)?.skuID
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    // MARK: - hero

    private func hero(_ product: Product) -> some View {
        VStack(spacing: 12) {
            ProductThumb(url: product.imageURL, side: 180)
                .frame(maxWidth: .infinity)
            HStack(spacing: 8) {
                if !product.sla.isEmpty {
                    stat("clock", product.sla)
                }
                if !product.rating.isEmpty {
                    stat("star.fill", product.rating
                        + (product.ratingCount.isEmpty ? "" : " (\(product.ratingCount))"))
                }
            }
        }
        .padding(.horizontal, 16)
    }

    private func stat(_ icon: String, _ text: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: icon).font(.system(size: 10, weight: .semibold))
            Text(text).font(.system(size: 11, weight: .semibold))
        }
        .foregroundStyle(theme.textSubtle)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(theme.bgSubtle, in: .capsule)
    }

    // MARK: - where Instamart advertises, we answer

    private func verdict(_ product: Product) -> some View {
        HStack(spacing: 9) {
            Image(systemName: product.buyable ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                .font(.system(size: 14))
                .foregroundStyle(product.buyable ? theme.primary : theme.notice)
            Text(product.blockedReason
                ?? "Inside your rule — the agent can buy this on its own.")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(product.buyable ? theme.textSubtle : theme.notice)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(
            (product.buyable ? theme.primary : theme.notice).opacity(0.10),
            in: .rect(cornerRadius: 14, style: .continuous)
        )
        .padding(.horizontal, 16)
    }

    // MARK: - brand, name, pills

    private func heading(_ product: Product) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if !product.brand.isEmpty {
                Eyebrow(text: product.brand, color: theme.primary)
            }
            Text(product.name)
                .font(.system(size: 21, weight: .semibold))
                .foregroundStyle(theme.textNormal)
                .fixedSize(horizontal: false, vertical: true)
            let pills = (product.veg == true ? ["VEG"] : []) + product.badges.prefix(2)
            if !pills.isEmpty {
                HStack(spacing: 6) {
                    ForEach(pills, id: \.self) { pill in
                        Text(pill)
                            .font(.system(size: 10, weight: .bold))
                            .kerning(0.5)
                            .foregroundStyle(theme.textSubtle)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .overlay(
                                RoundedRectangle(cornerRadius: 5, style: .continuous)
                                    .stroke(theme.borderSubtle, lineWidth: 1)
                            )
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
    }

    // MARK: - the packs, each with its own answer

    private func packs(_ product: Product) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(text: "Pack", color: theme.textMuted).padding(.horizontal, 16)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(product.variants) { variant in
                        tile(variant, buyable: product.buyable)
                    }
                }
                .padding(.horizontal, 16)
            }
            .scrollClipDisabled()
            if product.variants.contains(where: { !$0.withinCap }) {
                Text("Struck-through packs cost more than your per-order cap. "
                    + "You can still put one on a list — it will come back to you "
                    + "for approval instead of going out on its own.")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 16)
            }
        }
    }

    private func tile(_ variant: Pack, buyable: Bool) -> some View {
        let picked = variant.skuID == pack?.skuID
        let refused = !variant.withinCap || !buyable
        return Button {
            withAnimation(Motion.follow) { chosen = variant.skuID }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                if variant.discounted {
                    Text("\(variant.off)% OFF")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(theme.primary, in: .rect(cornerRadius: 4))
                } else {
                    Color.clear.frame(height: 15)
                }
                Text(variant.label.isEmpty ? "1 pack" : variant.label)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(theme.textNormal)
                    .lineLimit(1)
                if !variant.unitPrice.isEmpty {
                    Text(variant.unitPrice)
                        .font(.system(size: 10))
                        .foregroundStyle(theme.textMuted)
                        .lineLimit(1)
                }
                HStack(spacing: 5) {
                    Text(rupees(variant.pricePaise))
                        .font(.system(size: 15, weight: .semibold))
                        .monospacedDigit()
                        .strikethrough(refused, color: theme.notice)
                        .foregroundStyle(refused ? theme.textMuted : theme.textNormal)
                    if variant.discounted {
                        Text(rupees(variant.mrpPaise))
                            .font(.system(size: 11))
                            .monospacedDigit()
                            .strikethrough()
                            .foregroundStyle(theme.textMuted)
                    }
                }
                if !variant.inStock {
                    Text("out of stock")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(theme.textMuted)
                } else if !variant.withinCap {
                    Text("over your cap")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(theme.notice)
                }
            }
            .frame(width: 116, alignment: .leading)
            .padding(12)
            .background(theme.bgSubtle.opacity(picked ? 1 : 0.45),
                        in: .rect(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(picked ? theme.primary : theme.borderSubtle,
                            lineWidth: picked ? 2 : 1)
            )
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
        .accessibilityLabel(
            "\(variant.label), \(rupees(variant.pricePaise))"
            + (variant.withinCap ? "" : ", over your cap")
        )
    }

    // MARK: - what else would do

    private func rail(_ detail: ProductDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(
                text: detail.comparable ? "The same thing elsewhere" : "Similar products",
                color: theme.textMuted
            )
            .padding(.horizontal, 16)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(detail.alternatives) { alt in
                        alternative(alt, comparable: detail.comparable)
                    }
                }
                .padding(.horizontal, 16)
            }
            .scrollClipDisabled()
        }
    }

    private func alternative(_ alt: Product, comparable: Bool) -> some View {
        Button {
            // A shop is not a product: on the mock, tapping Blinkit's row would
            // reload the same name and change nothing, so it swaps instead.
            if comparable {
                onSwap.map { $0(alt.name) }
                dismiss()
            } else {
                withAnimation(Motion.follow) { showing = alt.name }
            }
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                ProductThumb(url: alt.imageURL, side: 96)
                Text(alt.name)
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textNormal)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(height: 30, alignment: .top)
                HStack(spacing: 5) {
                    Text(rupees(alt.cheapest?.pricePaise ?? 0))
                        .font(.system(size: 13, weight: .semibold))
                        .monospacedDigit()
                        .foregroundStyle(alt.buyable ? theme.textNormal : theme.textMuted)
                    if comparable {
                        Text(alt.merchant)
                            .font(.system(size: 10))
                            .foregroundStyle(theme.textMuted)
                            .lineLimit(1)
                    }
                }
                if !alt.buyable {
                    Text("outside your rule")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(theme.notice)
                        .lineLimit(1)
                }
            }
            .frame(width: 108, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
        .disabled(comparable && onSwap == nil)
    }

    // MARK: - the one action

    private var bar: some View {
        HStack(spacing: 14) {
            if let pack {
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text(rupees(pack.pricePaise))
                            .font(.system(size: 19, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(theme.textNormal)
                        if pack.discounted {
                            Text(rupees(pack.mrpPaise))
                                .font(.system(size: 12))
                                .monospacedDigit()
                                .strikethrough()
                                .foregroundStyle(theme.textMuted)
                        }
                    }
                    Text(pack.label.isEmpty ? "1 pack" : pack.label)
                        .font(.system(size: 11))
                        .foregroundStyle(theme.textMuted)
                }
                Spacer(minLength: 0)
                Button {
                    onSwap.map { $0(pack.name) }
                    dismiss()
                } label: {
                    Text(onSwap == nil ? "Close" : "Use this on my list")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 20)
                        .frame(height: 46)
                        .background(theme.primary, in: .capsule)
                }
                .buttonStyle(.pressable)
                .disabled(onSwap == nil)
                .opacity(onSwap == nil ? 0.45 : 1)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.bar)
    }
}
