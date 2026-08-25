# Captured Swiggy payloads

`swiggy_search.json` and `swiggy_cart.json` are **synthetic** — built from the
field names documented at `mcp.swiggy.com/builders/docs/reference/instamart/`,
not captured from a live session. They pin the parser against the documented
*shape*.

They do **not** settle the one thing the docs leave open: whether money fields
are rupees or paise. `to_paise` currently reads them as rupees. S0 replaces
these two files with real payloads, and if that assumption is wrong the money
tests fail loudly rather than a cart being off by a factor of a hundred.

Capture with the MCP connected:

    get_cart()                                  -> swiggy_cart.json
    search_products(addressId=..., query="milk") -> swiggy_search.json
