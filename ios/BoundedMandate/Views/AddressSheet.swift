import SwiftUI

/// Where things get delivered — the third thing the user owns.
///
/// A mandate that bounds the cap, the shop and the scope is still worth nothing
/// if an agent can move the doorstep: ₹1,900 of perfectly ordinary groceries,
/// entirely in policy, sent to a stranger. So this is a picker over the user's
/// *own* account addresses, and choosing one is what authorises it.
///
/// The engine matches on the address **id**, never on the text below it. Swiggy
/// returns two different strings for one address depending which endpoint you
/// ask, so a policy pinned to prose is refused against its own doorstep.
@MainActor @Observable
final class AddressStore {
    private(set) var addresses: [DeliveryAddress] = []
    private(set) var problem: String?
    private(set) var saving = false

    var selected: DeliveryAddress? { addresses.first(where: \.selected) }

    func load() async {
        do {
            addresses = try await Engine.readAddresses()
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    func choose(_ address: DeliveryAddress) async {
        guard !address.selected else { return }
        saving = true
        defer { saving = false }
        do {
            addresses = try await Engine.chooseAddress(address.addressID)
            problem = nil
        } catch {
            problem = error.localizedDescription
            await load()
        }
    }
}

struct AddressSheet: View {
    @Environment(\.theme) private var theme
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var store = AddressStore()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if store.addresses.isEmpty, store.problem == nil {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                    }

                    Card {
                        VStack(spacing: 0) {
                            ForEach(Array(store.addresses.enumerated()), id: \.element.id) {
                                index, address in
                                if index > 0 { Divider().overlay(theme.borderSubtle) }
                                row(address)
                            }
                        }
                    }

                    if let problem = store.problem {
                        Text(problem)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.negative)
                            .textSelection(.enabled)
                    }

                    Text(
                        "Orders go to the address you pick here, and your rule authorises "
                      + "that one. The agent reads it and has no way to change it — "
                      + "neither does a one-time approval."
                    )
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textMuted)
                    .padding(.horizontal, 4)
                }
                .padding(16)
            }
            .background(Backdrop())
            .navigationTitle("Delivering to")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .task { await store.load() }
    }

    private func row(_ address: DeliveryAddress) -> some View {
        Button {
            Task { await store.choose(address) }
        } label: {
            HStack(alignment: .top, spacing: 12) {
                // The check is the whole state of the row: selected *is*
                // authorised, because every address here is already the user's.
                Image(systemName: address.selected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 18))
                    .foregroundStyle(address.selected ? theme.primary : theme.textMuted)

                VStack(alignment: .leading, spacing: 3) {
                    Text(address.label)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(theme.textNormal)
                    Text(address.line)
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .contentShape(.rect)
        }
        .buttonStyle(.pressable)
        .accessibilityValue(address.selected ? "selected" : "not selected")
        .disabled(store.saving)
        .animation(Motion.respectful(Motion.enter(), reduced: reduceMotion), value: address.selected)
    }
}
