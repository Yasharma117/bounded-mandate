import SwiftUI

/// The user's list, and the one card they come back to.
///
/// It answers three questions in the order people actually ask them: what is on
/// it, what will it cost, and will that clear my rule. The cap meter is the
/// reason the third question gets answered here instead of at checkout — a list
/// that cannot clear the policy should say so while it is still editable, not
/// after an agent run reports an escalation.
struct ShoppingListCard: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL

    let list: ShoppingList
    var editable = true
    var onRemove: ((ListItem) -> Void)?
    var onAdd: (() -> Void)?

    /// A twelve-item list is taller than the screen, and a card that fills the
    /// thread stops being punctuation and becomes a page. Collapsed by default;
    /// the total and the cap meter are above the fold either way, because those
    /// are what the reader came for.
    @State private var expanded = false
    private static let collapsedCount = 4

    private var meterColor: Color { list.overCap ? theme.negative : theme.primary }
    private var visible: [ListItem] {
        expanded ? list.items : Array(list.items.prefix(Self.collapsedCount))
    }
    private var hiddenCount: Int { max(0, list.items.count - Self.collapsedCount) }

    var body: some View {
        Card(tint: list.overCap ? theme.negative : nil) {
            VStack(alignment: .leading, spacing: 0) {
                header
                Divider().overlay(theme.borderSubtle)
                items
                expander
                Divider().overlay(theme.borderSubtle)
                footer
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 7) {
                Image(systemName: list.overCap
                    ? "exclamationmark.triangle.fill" : "list.bullet.rectangle")
                    .font(.system(size: 13))
                    .foregroundStyle(meterColor)
                Eyebrow(text: list.name, color: meterColor)
                Spacer()
                Text(list.spent ? "ordered" : list.merchant)
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textMuted)
            }

            VStack(alignment: .leading, spacing: 7) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(rupees(list.totalPaise))
                        .font(.system(size: 30, weight: .bold))
                        .monospacedDigit()
                        .kerning(-0.6)
                        .foregroundStyle(theme.textNormal)
                    Text("of \(rupees(list.capPaise)) per order")
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textMuted)
                }

                // The meter is the whole point of showing a cap at all: a number
                // beside a number is arithmetic the reader has to do themselves.
                GeometryReader { geometry in
                    ZStack(alignment: .leading) {
                        Capsule().fill(theme.borderSubtle)
                        Capsule()
                            .fill(meterColor)
                            .frame(width: geometry.size.width * min(list.capUsed, 1))
                    }
                }
                .frame(height: 5)
                // Removing an item should show the headroom coming back. A
                // meter that jumps to its new value tells you the number
                // changed; one that travels tells you which way.
                .animation(
                    Motion.respectful(Motion.move(0.28), reduced: reduceMotion),
                    value: list.capUsed
                )
                .animation(
                    Motion.respectful(Motion.enter(0.2), reduced: reduceMotion),
                    value: list.overCap
                )

                Text(
                    list.overCap
                        ? "\(rupees(-list.headroomPaise)) over your cap — this will need you"
                        : "\(rupees(list.headroomPaise)) left before it needs you"
                )
                .font(.system(size: 12))
                .foregroundStyle(list.overCap ? theme.negative : theme.textMuted)
            }
        }
        .padding(18)
    }

    private var items: some View {
        VStack(spacing: 0) {
            ForEach(Array(visible.enumerated()), id: \.element.id) { index, item in
                if index > 0 {
                    Divider().overlay(theme.borderSubtle.opacity(0.5)).padding(.leading, 18)
                }
                ItemRow(item: item, editable: editable, onRemove: onRemove) {
                    if let url = URL(string: Engine.baseURL.absoluteString + item.url) {
                        openURL(url)
                    }
                }
            }
        }
    }

    @ViewBuilder private var expander: some View {
        if hiddenCount > 0 {
            Divider().overlay(theme.borderSubtle.opacity(0.5)).padding(.leading, 18)
            Button {
                withAnimation(Motion.respectful(Motion.move(), reduced: reduceMotion)) { expanded.toggle() }
            } label: {
                HStack(spacing: 5) {
                    Text(expanded ? "Show less" : "\(hiddenCount) more")
                        .font(.system(size: 14, weight: .medium))
                    Image(systemName: "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .foregroundStyle(theme.primary)
                .frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.pressable)
        }
    }

    private var footer: some View {
        HStack {
            if editable, let onAdd {
                Button(action: onAdd) {
                    Label("Add an item", systemImage: "plus")
                        .font(.system(size: 14, weight: .medium))
                }
                .buttonStyle(.pressable)
                .foregroundStyle(theme.primary)
                .frame(minHeight: 44)
            }
            Spacer()
            Text(plural(list.items.count, "item"))
                .font(.system(size: 13))
                .monospacedDigit()
                .foregroundStyle(theme.textMuted)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, editable ? 4 : 14)
    }
}

/// One line: what it is, what it costs, and a way to go look at it.
struct ItemRow: View {
    @Environment(\.theme) private var theme
    let item: ListItem
    var editable = false
    var onRemove: ((ListItem) -> Void)?
    var onOpen: (() -> Void)?

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(.system(size: 15))
                    .foregroundStyle(theme.textNormal)
                    .lineLimit(2)
                if item.unstocked {
                    Text("not stocked here")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.notice)
                }
            }

            Spacer(minLength: 8)

            if let paise = item.pricePaise {
                Text(rupees(paise))
                    .font(.system(size: 15))
                    .monospacedDigit()
                    .foregroundStyle(theme.textSubtle)
            }

            if onOpen != nil {
                Button(action: { onOpen?() }) {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(theme.textMuted)
                        .frame(width: 30, height: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.pressable)
            }

            if editable, let onRemove {
                Button(action: { onRemove(item) }) {
                    Image(systemName: "minus.circle.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(theme.textMuted.opacity(0.6))
                        .frame(width: 32, height: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.pressable)
            }
        }
        .padding(.leading, 18)
        .padding(.trailing, editable || onOpen != nil ? 8 : 18)
        .padding(.vertical, 5)
        .contentShape(.rect)
    }
}
