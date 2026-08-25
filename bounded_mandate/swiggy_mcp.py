"""Speaking MCP to Swiggy over HTTP, so the engine holds its own session.

This exists because of one line in the security argument: an engine that asks
the *agent* to call `get_cart` and relay the answer has verified nothing. Layer 0
only means something if the engine reads the merchant itself. So the engine is
its own MCP client rather than borrowing one.

## What the docs pin down, and what they do not

Verified at `mcp.swiggy.com/builders`:

- Transport is **streamable HTTP** — "Swiggy Builders Club speaks Model Context
  Protocol over streamable HTTP."
- Auth is `Authorization: Bearer $SWIGGY_TOKEN`, from OAuth 2.1 + PKCE (S256).
- Rate limits are 70 req/min per server, **30 req/min for write tools**, burst 2x
  over a 10s window, with `Retry-After` on 429.

**Not pinned:** the docs give `https://mcp.swiggy.com/food` and `/dineout` but
never spell out the Instamart path. `SWIGGY_MCP_URL` therefore defaults to the
`/im` given in the integration brief and stays overridable, rather than this
module inventing a URL and pretending it read it somewhere.

## The token is a five-day, human-issued thing

There are no API keys and no service accounts. Access tokens last five days, and
`refresh_token` is advertised in the metadata but **not wired in v1.0** — so
there is no silent renewal. Every re-authorisation is a person completing phone
+ OTP in a browser.

That is worth stating plainly rather than hiding, because it inverts the usual
assumption: the Razorpay mandate is durable authority that outlives any session,
while the merchant session is a five-day interactive credential. The weakest link
in an unattended agent turns out to be the shop's login, not the money.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx2 as httpx

#: Undocumented for Instamart; `/im` comes from the integration brief.
BASE_URL = os.environ.get("SWIGGY_MCP_URL", "https://mcp.swiggy.com/im")
TIMEOUT = 30.0

#: Documented limits: 70 req/min per server, 30 req/min on write tools. Reads
#: get the faster floor because building a twelve-item basket is a dozen
#: searches, and pacing those at the write rate would take half a minute.
READ_INTERVAL = 0.9
WRITE_INTERVAL = 2.1
WRITE_TOOLS = frozenset({"update_cart", "clear_cart"})

PROTOCOL_VERSION = "2025-06-18"


class SwiggySessionError(RuntimeError):
    """No token, an expired one, or the server refused."""


class SwiggyMCP:
    """One MCP session. Initialises lazily, then calls tools.

    Deliberately has no `call_any` escape hatch: `SwiggyAdapter` checks its own
    allowlist before it gets here, and this class exists to move bytes, not to
    decide what may be called.
    """

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self._token = token or os.environ.get("SWIGGY_ACCESS_TOKEN", "").strip()
        self._base = (base_url or BASE_URL).rstrip("/")
        self._session_id: str | None = None
        self._next_id = 1
        self._last_call = 0.0

    def __call__(self, tool: str, **params: Any) -> dict:
        """The `CallTool` shape `SwiggyAdapter` expects."""
        if not self._token:
            raise SwiggySessionError(
                "SWIGGY_ACCESS_TOKEN is not set. Swiggy issues no API keys — run the "
                "OAuth flow (phone + OTP in a browser) and paste the access token. "
                "It lasts five days and there is no refresh in v1.0."
            )
        if self._session_id is None:
            self._initialise()
        interval = WRITE_INTERVAL if tool in WRITE_TOOLS else READ_INTERVAL
        result = self._rpc("tools/call", {"name": tool, "arguments": params}, interval)
        return _unwrap(result)

    # --- transport ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            # Streamable HTTP may answer with either, and which one is the
            # server's choice per request.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _initialise(self) -> None:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._take_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "bounded-mandate", "version": "0.1.0"},
                },
            }
        )
        # The session id arrives as a header and every later call must carry it.
        self._session_id = response.headers.get("Mcp-Session-Id") or ""
        _result_of(_parse(response))
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_body=False)

    def _rpc(self, method: str, params: dict, interval: float = READ_INTERVAL) -> Any:
        response = self._post(
            {"jsonrpc": "2.0", "id": self._take_id(), "method": method, "params": params},
            interval=interval,
        )
        return _result_of(_parse(response))

    def _post(
        self, body: dict, *, expect_body: bool = True, interval: float = READ_INTERVAL
    ) -> httpx.Response:
        # Serialise calls and hold the floor between them. Cheaper than a bucket
        # and enough for a cart loop; a 429 is still handled below because
        # "enough" is not "guaranteed".
        wait = interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

        for attempt in range(3):
            try:
                response = httpx.post(
                    self._base, headers=self._headers(), json=body, timeout=TIMEOUT
                )
            except httpx.HTTPError as exc:
                raise SwiggySessionError(f"Swiggy MCP unreachable: {exc}") from exc
            finally:
                self._last_call = time.monotonic()

            if response.status_code == 429 and attempt < 2:
                time.sleep(float(response.headers.get("Retry-After", "2")))
                continue
            if response.status_code in (401, 403):
                raise SwiggySessionError(
                    "Swiggy rejected the token. Access tokens last five days and "
                    "cannot be refreshed in v1.0 — re-run the OAuth flow."
                )
            if response.status_code >= 400:
                raise SwiggySessionError(
                    f"Swiggy MCP {response.status_code}: {response.text[:200]}"
                )
            return response

        raise SwiggySessionError("Swiggy MCP rate limited three times over")

    def _take_id(self) -> int:
        self._next_id += 1
        return self._next_id


# --- decoding ---------------------------------------------------------------


def _parse(response: httpx.Response) -> dict:
    """A streamable-HTTP answer is either JSON or an SSE frame carrying JSON."""
    if not response.content:
        return {}
    if "text/event-stream" in response.headers.get("Content-Type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    return json.loads(payload)
        return {}
    return response.json()


def _result_of(message: dict) -> Any:
    if error := message.get("error"):
        raise SwiggySessionError(f"Swiggy MCP error {error.get('code')}: {error.get('message')}")
    return message.get("result")


def _unwrap(result: Any) -> dict:
    """MCP wraps a tool's answer in `content[]`; the payload is the JSON inside.

    Returns `{}` rather than raising on a shape this does not recognise —
    `SwiggyAdapter` treats an empty payload as "no cart", which fails closed.
    """
    if not isinstance(result, dict):
        return {}
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text") or "")
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}
