# Captured Swiggy payloads

`swiggy_search.json` and `swiggy_cart.json` are **real**, captured from a live
Instamart session on 2026-08-26 against a Gurugram address.

They exist because the documented reference pages do not describe the payloads
accurately, and four things only a real capture could settle turned up here:

- `toPay` is `{"label": ..., "value": ...}`, not a scalar.
- Bill values carry the rupee sign and can read `FREE` instead of `0`.
- A cart line names itself `itemName` + `itemVariant`; the search endpoint calls
  the same thing `displayName`.
- `update_cart` needs **both** `spinId` and `skuId`. Sending only the sku is
  accepted and silently adds nothing.

Money is rupees with paise as decimals, confirmed: Amul Taaza 200 ml came back
as `mrp: 17` with `unitLevelPrice: "8.5/100 ml"`.

Recapture with the MCP connected:

    get_cart()                                   -> swiggy_cart.json
    search_products(addressId=..., query="...")  -> swiggy_search.json
