import Foundation

/// One address on the user's own account.
///
/// Three fields, because three different parties read them: the engine matches
/// on `addressID`, the user recognises `label`, and `line` is what the card
/// shows. Authority never travels as prose — the same Swiggy address comes back
/// formatted two different ways depending which endpoint answered, so a policy
/// pinned to the text is refused against its own doorstep.
struct DeliveryAddress: Decodable, Hashable, Sendable, Identifiable {
    let addressID: String
    let label: String
    let line: String
    /// Where orders currently go.
    let selected: Bool
    /// Whether the standing rule permits delivery here. Selecting is what makes
    /// it true — every row is already the user's own address, so there is no
    /// third party to introduce.
    let authorised: Bool

    var id: String { addressID }

    enum CodingKeys: String, CodingKey {
        case addressID = "address_id"
        case label, line, selected, authorised
    }
}
