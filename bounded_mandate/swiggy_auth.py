"""Getting a Swiggy token, which is a thing a person has to do.

    uv run python -m bounded_mandate.swiggy_auth

Opens a browser, waits for you to finish phone + OTP, catches the redirect and
writes `SWIGGY_ACCESS_TOKEN` to `.env`.

## Why this is a script and not a service

Swiggy issues no API keys and no service accounts. Every token comes from an
OAuth 2.1 authorization-code flow with PKCE where the consent screen collects a
phone number and an OTP, so there is no headless path — not one that is awkward,
one that does not exist. Tokens last five days. `refresh_token` is advertised in
the metadata and the docs say issuance is not wired in v1.0, so this asks for one
and simply records whether it arrived rather than assuming either way.

Nothing here is hardcoded from a blog post: the endpoints come from
`/.well-known/oauth-authorization-server` at runtime, and the client id comes
from Dynamic Client Registration, because Swiggy issues no client id to apply
for. If Swiggy moves an endpoint, this follows.

## What it does to your account

Registers an OAuth client and obtains a token scoped to `mcp:tools`. It does not
call a single Instamart tool — `SwiggyAdapter` does that, and it cannot reach
checkout. This file only gets you a credential.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx2 as httpx

METADATA_URL = os.environ.get(
    "SWIGGY_OAUTH_METADATA", "https://mcp.swiggy.com/.well-known/oauth-authorization-server"
)
#: The docs say steps 1–5 run on localhost, free, with no approval.
REDIRECT_HOST = "127.0.0.1"
SCOPE = "mcp:tools"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class AuthFailed(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((REDIRECT_HOST, 0))
        return probe.getsockname()[1]


def discover() -> dict[str, Any]:
    """Endpoints from the server, not from memory."""
    response = httpx.get(METADATA_URL, timeout=20.0)
    if response.status_code != 200:
        raise AuthFailed(f"could not read OAuth metadata: {response.status_code}")
    metadata = response.json()
    for required in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not metadata.get(required):
            raise AuthFailed(f"OAuth metadata has no {required}")
    if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
        raise AuthFailed("server does not offer PKCE S256; refusing to fall back to plain")
    return metadata


def register(metadata: dict, redirect_uri: str) -> str:
    """Dynamic Client Registration. Swiggy issues no client id to apply for."""
    response = httpx.post(
        metadata["registration_endpoint"],
        json={
            "client_name": "Bounded Mandate",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # A public client: there is no secret to keep, and PKCE is what
            # actually binds the code to this process.
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise AuthFailed(f"registration refused ({response.status_code}): {response.text[:300]}")
    client_id = response.json().get("client_id")
    if not client_id:
        raise AuthFailed(f"registration returned no client_id: {response.text[:300]}")
    return str(client_id)


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


class _Catcher(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect and says something friendly in the browser."""

    result: dict[str, str] = {}
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802 — stdlib's spelling
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.result = {k: v[0] for k, v in query.items()}
        ok = "code" in _Catcher.result
        body = (
            "<h2>Signed in.</h2><p>You can close this tab and go back to the terminal.</p>"
            if ok
            else f"<h2>Not signed in.</h2><pre>{_Catcher.result}</pre>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())
        _Catcher.done.set()

    def log_message(self, *_: Any) -> None:
        return  # the terminal is narrating; the server should not


def authorise(metadata: dict, client_id: str, redirect_uri: str, port: int) -> str:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    url = (
        metadata["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    server = http.server.HTTPServer((REDIRECT_HOST, port), _Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("\n  Opening your browser for phone + OTP.")
    print("  If it does not open, paste this:\n")
    print(f"    {url}\n")
    webbrowser.open(url)

    if not _Catcher.done.wait(timeout=300):
        server.shutdown()
        raise AuthFailed("timed out waiting for the browser (5 minutes)")
    server.shutdown()

    got = _Catcher.result
    if got.get("error"):
        raise AuthFailed(f"{got['error']}: {got.get('error_description', '')}")
    # Without this an attacker who could reach the loopback listener could feed
    # it a code from a session you did not start.
    if got.get("state") != state:
        raise AuthFailed("state did not match; refusing the code")
    if not got.get("code"):
        raise AuthFailed(f"no code in the redirect: {got}")
    return json.dumps({"code": got["code"], "verifier": verifier})


def exchange(metadata: dict, client_id: str, redirect_uri: str, handoff: str) -> dict:
    parcel = json.loads(handoff)
    response = httpx.post(
        metadata["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": parcel["code"],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": parcel["verifier"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise AuthFailed(f"token exchange refused ({response.status_code}): {response.text[:300]}")
    token = response.json()
    if not token.get("access_token"):
        raise AuthFailed(f"no access_token in the response: {response.text[:300]}")
    return token


def write_env(token: dict) -> None:
    """Put the token in `.env`, replacing any previous one.

    Written here rather than printed so the five-day token does not end up in
    shell history or a scrollback buffer someone screenshots.
    """
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    kept = [ln for ln in lines if not ln.startswith("SWIGGY_ACCESS_TOKEN=")]
    kept.append(f"SWIGGY_ACCESS_TOKEN={token['access_token']}")
    ENV_PATH.write_text("\n".join(kept) + "\n")
    ENV_PATH.chmod(0o600)


def main() -> int:
    port = _free_port()
    redirect_uri = f"http://{REDIRECT_HOST}:{port}/callback"

    print("  Reading OAuth metadata…")
    metadata = discover()
    print(f"    authorize  {metadata['authorization_endpoint']}")
    print(f"    token      {metadata['token_endpoint']}")

    print("  Registering a client…")
    client_id = register(metadata, redirect_uri)
    print(f"    client_id  {client_id}")

    handoff = authorise(metadata, client_id, redirect_uri, port)
    print("  Exchanging the code…")
    token = exchange(metadata, client_id, redirect_uri, handoff)

    write_env(token)
    seconds = token.get("expires_in")
    print("\n  SWIGGY_ACCESS_TOKEN written to .env")
    if seconds:
        print(f"  Valid for {int(seconds) // 86_400} days ({seconds}s).")
    # The docs say refresh issuance is not wired in v1.0. Report what actually
    # arrived rather than repeating either claim.
    print(
        "  Refresh token: "
        + ("issued." if token.get("refresh_token") else "not issued — re-run this when it expires.")
    )
    print("\n  Next: pin an address with")
    print("    uv run python -m bounded_mandate.swiggy_auth --addresses\n")
    return 0


def show_addresses() -> int:
    """`search_products` needs an addressId that cannot be invented."""
    from .swiggy_mcp import SwiggyMCP

    payload = SwiggyMCP()("get_addresses")
    rows = payload.get("addresses") or []
    if not rows:
        print("  This account has no delivery address. Add one in the Swiggy app first.")
        return 1
    print("\n  Addresses on this account:\n")
    # Field names taken from a real payload, not guessed: `addressLine`,
    # `addressTag`, `addressCategory`.
    for row in rows:
        identifier = row.get("id") or row.get("addressId") or ""
        tag = row.get("addressTag") or row.get("addressCategory") or ""
        where = row.get("addressLine") or ""
        print(f"    {identifier:<22} {tag:<8} {where[:64]}")
    print("\n  Serviceability varies by address, so pin one rather than")
    print("  discovering it on camera:\n")
    print(f"    SWIGGY_ADDRESS_ID={rows[0].get('id') or rows[0].get('addressId')}\n")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(show_addresses() if "--addresses" in sys.argv else main())
