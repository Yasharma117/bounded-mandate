"""Getting a token, and the parts of that which can be checked without a phone.

The browser step cannot be tested here — Swiggy's consent screen collects a
phone number and an OTP, which is the whole reason this is a script a person
runs rather than something the engine does. Everything either side of that
step is testable, and is.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from bounded_mandate import swiggy_auth
from bounded_mandate.swiggy_auth import AuthFailed, _pkce, discover, exchange, write_env


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or str(payload)

    def json(self):
        return self._payload


GOOD_METADATA = {
    "issuer": "https://mcp.swiggy.com/auth",
    "authorization_endpoint": "https://mcp.swiggy.com/auth/authorize",
    "token_endpoint": "https://mcp.swiggy.com/auth/token",
    "registration_endpoint": "https://mcp.swiggy.com/auth/register",
    "code_challenge_methods_supported": ["S256"],
}


class TestPKCE:
    def test_the_challenge_is_the_hash_of_the_verifier(self):
        verifier, challenge = _pkce()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        assert challenge == expected.decode().rstrip("=")

    def test_a_fresh_verifier_every_time(self):
        assert len({_pkce()[0] for _ in range(20)}) == 20

    def test_the_verifier_is_long_enough_to_be_worth_hashing(self):
        """RFC 7636 wants 43–128 characters. A short one is guessable, and a
        guessable verifier makes PKCE decorative."""
        verifier, _ = _pkce()
        assert 43 <= len(verifier) <= 128


class TestDiscovery:
    def test_endpoints_come_from_the_server(self, monkeypatch):
        monkeypatch.setattr(
            swiggy_auth.httpx, "get", lambda *a, **k: FakeResponse(200, GOOD_METADATA)
        )
        assert discover()["token_endpoint"] == GOOD_METADATA["token_endpoint"]

    def test_a_server_without_s256_is_refused(self, monkeypatch):
        """Downgrading to `plain` would leave the code unbound to this process,
        which is the one thing PKCE is for."""
        weak = dict(GOOD_METADATA, code_challenge_methods_supported=["plain"])
        monkeypatch.setattr(swiggy_auth.httpx, "get", lambda *a, **k: FakeResponse(200, weak))
        with pytest.raises(AuthFailed, match="PKCE"):
            discover()

    def test_missing_endpoints_fail_rather_than_defaulting(self, monkeypatch):
        """Falling back to a remembered URL is how you end up posting a token
        request at whatever used to be there."""
        for absent in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            partial = {k: v for k, v in GOOD_METADATA.items() if k != absent}
            monkeypatch.setattr(
                swiggy_auth.httpx,
                "get",
                lambda *a, _p=partial, **k: FakeResponse(200, _p),
            )
            with pytest.raises(AuthFailed, match=absent):
                discover()


class TestExchange:
    def handoff(self):
        return '{"code": "abc", "verifier": "v"}'

    def test_the_verifier_is_sent_with_the_code(self, monkeypatch):
        sent: dict = {}

        def capture(url, data=None, **_):
            sent.update(data or {})
            return FakeResponse(200, {"access_token": "tok", "expires_in": 432_000})

        monkeypatch.setattr(swiggy_auth.httpx, "post", capture)
        token = exchange(GOOD_METADATA, "cid", "http://127.0.0.1:1/cb", self.handoff())
        assert token["access_token"] == "tok"
        assert sent["code_verifier"] == "v"
        assert sent["grant_type"] == "authorization_code"

    def test_a_response_without_a_token_is_a_failure_not_an_empty_string(self, monkeypatch):
        monkeypatch.setattr(
            swiggy_auth.httpx, "post", lambda *a, **k: FakeResponse(200, {"token_type": "Bearer"})
        )
        with pytest.raises(AuthFailed, match="no access_token"):
            exchange(GOOD_METADATA, "cid", "http://127.0.0.1:1/cb", self.handoff())

    def test_a_refusal_carries_the_reason(self, monkeypatch):
        monkeypatch.setattr(
            swiggy_auth.httpx,
            "post",
            lambda *a, **k: FakeResponse(400, {}, "invalid_grant"),
        )
        with pytest.raises(AuthFailed, match="invalid_grant"):
            exchange(GOOD_METADATA, "cid", "http://127.0.0.1:1/cb", self.handoff())


class TestEnvFile:
    def test_the_token_replaces_a_previous_one(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("NVIDIA_API_KEY=keep-me\nSWIGGY_ACCESS_TOKEN=old\n")
        monkeypatch.setattr(swiggy_auth, "ENV_PATH", env)

        write_env({"access_token": "new"})
        body = env.read_text()
        assert "SWIGGY_ACCESS_TOKEN=new" in body
        assert "old" not in body
        assert "NVIDIA_API_KEY=keep-me" in body, "other credentials must survive"

    def test_the_file_is_not_world_readable(self, tmp_path, monkeypatch):
        """A five-day token is still a credential."""
        env = tmp_path / ".env"
        monkeypatch.setattr(swiggy_auth, "ENV_PATH", env)
        write_env({"access_token": "tok"})
        assert oct(env.stat().st_mode)[-3:] == "600"

    def test_the_token_is_never_printed(self):
        """It goes to a file rather than stdout so it does not end up in shell
        history or a scrollback someone screenshots."""
        import inspect

        source = inspect.getsource(swiggy_auth.main)
        assert "access_token" not in source.replace('token.get("refresh_token")', "")
