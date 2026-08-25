"""Voice is a channel, not an authority. These pin that it stays one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bounded_mandate import voice, web


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code, self._payload, self.content = status_code, payload, content
        self.reason_phrase = "Fake"
        self.text = "provider said no"

    def json(self):
        return self._payload


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key")


@pytest.fixture
def calls(monkeypatch):
    """Capture what would go over the wire instead of going over it."""
    seen = []

    def fake_post(url, **kwargs):
        seen.append({"url": url, **kwargs})
        return FakeResponse(payload={"text": " order my usual groceries  "}, content=b"ID3mp3")

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    return seen


def test_no_key_configured_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(voice.VoiceUnavailable, match="not configured"):
        voice.transcribe(b"audio")


def test_transcribe_sends_the_field_names_elevenlabs_documents(key, calls):
    assert voice.transcribe(b"raw-audio", filename="clip.m4a") == "order my usual groceries"
    (call,) = calls
    assert call["url"].endswith("/speech-to-text")
    assert call["headers"]["xi-api-key"] == "sk_test_key"
    assert call["files"]["file"][0] == "clip.m4a"
    assert call["files"]["file"][1] == b"raw-audio"
    assert call["data"]["model_id"] == voice.STT_MODEL


def test_oversized_audio_is_refused_before_it_costs_anything(key, calls):
    with pytest.raises(voice.VoiceUnavailable, match="exceeds"):
        voice.transcribe(b"x" * (voice.MAX_AUDIO_BYTES + 1))
    assert calls == []


def test_empty_audio_is_refused(key, calls):
    with pytest.raises(voice.VoiceUnavailable, match="no audio"):
        voice.transcribe(b"")
    assert calls == []


def test_a_provider_error_surfaces_as_unavailable(key, monkeypatch):
    monkeypatch.setattr(voice.httpx, "post", lambda url, **kw: FakeResponse(status_code=401))
    with pytest.raises(voice.VoiceUnavailable, match="401"):
        voice.transcribe(b"audio")


def test_speak_targets_the_configured_voice_and_returns_audio(key, calls):
    spoken = voice.speak("Nothing was charged.", provider="elevenlabs")
    assert spoken.audio == b"ID3mp3"
    assert spoken.media_type == "audio/mpeg"
    assert spoken.provider == "elevenlabs"
    (call,) = calls
    assert call["url"].endswith(f"/text-to-speech/{voice.VOICE_ID}")
    assert call["json"]["text"] == "Nothing was charged."
    assert call["json"]["model_id"] == voice.TTS_MODEL


def test_speak_truncates_rather_than_billing_for_a_runaway_string(key, calls):
    voice.speak("a" * (voice.MAX_SPEAK_CHARS + 500), provider="elevenlabs")
    assert len(calls[0]["json"]["text"]) == voice.MAX_SPEAK_CHARS


def test_speak_refuses_whitespace(key, calls):
    with pytest.raises(voice.VoiceUnavailable, match="nothing to say"):
        voice.speak("   ")
    assert calls == []


# --- two services, one seam -------------------------------------------------


@pytest.fixture
def rumik_key(monkeypatch):
    monkeypatch.setenv("RUMIK_API_KEY", "rk_live_test")


def test_rumik_speaks_wav_and_says_so(rumik_key, calls, monkeypatch):
    monkeypatch.setattr(voice, "RUMIK_MODEL", "mulberry")
    spoken = voice.speak("Nothing was charged.", provider="rumik")
    assert spoken.media_type == "audio/wav"
    assert spoken.provider == "rumik"
    (call,) = calls
    assert call["url"].endswith("/tts")
    assert call["headers"]["Authorization"] == "Bearer rk_live_test"
    assert call["json"]["model"] == "mulberry"
    # mulberry is steered by a description; muga takes neither.
    assert call["json"]["description"]
    assert call["json"]["speaker"] == voice.RUMIK_SPEAKER


def test_muga_is_not_sent_mulberry_only_fields(rumik_key, calls, monkeypatch):
    monkeypatch.setattr(voice, "RUMIK_MODEL", "muga")
    voice.speak("Nothing was charged.", provider="rumik")
    assert "description" not in calls[0]["json"]
    assert "speaker" not in calls[0]["json"]


def test_each_service_needs_only_its_own_key(monkeypatch, calls):
    """Rumik must not require an ElevenLabs key, or the seam is not a seam."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("RUMIK_API_KEY", "rk_live_test")
    assert voice.speak("hello", provider="rumik").provider == "rumik"

    monkeypatch.delenv("RUMIK_API_KEY", raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key")
    assert voice.speak("hello", provider="elevenlabs").provider == "elevenlabs"


def test_an_unknown_provider_names_the_ones_that_exist(key, calls):
    with pytest.raises(voice.VoiceUnavailable, match="no such voice provider"):
        voice.speak("hello", provider="murf")
    assert calls == []


def test_hearing_is_elevenlabs_whichever_service_speaks(rumik_key, monkeypatch, calls):
    """Rumik does not transcribe. Swapping the speaker must not silently swap
    the listener to something that cannot listen."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(voice, "TTS_PROVIDER", "rumik")
    with pytest.raises(voice.VoiceUnavailable, match="ELEVENLABS_API_KEY"):
        voice.transcribe(b"audio")


# --- the HTTP surface ------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(web.app)


def test_transcribe_route_returns_the_text(client, key, calls):
    response = client.post("/api/voice/transcribe", content=b"raw-audio")
    assert response.status_code == 200
    assert response.json() == {"text": "order my usual groceries"}


def test_transcribe_route_rejects_an_empty_body(client, key, calls):
    assert client.post("/api/voice/transcribe", content=b"").status_code == 400
    assert calls == []


def test_voice_routes_degrade_to_503_without_a_key(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert client.post("/api/voice/transcribe", content=b"raw-audio").status_code == 503
    assert client.post("/api/voice/speak", json={"text": "hello"}).status_code == 503


def test_speak_route_returns_mp3_bytes(client, key, calls):
    response = client.post("/api/voice/speak", json={"text": "Nothing was charged."})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3mp3"


def test_a_transcript_cannot_reach_the_engine_on_its_own(client, key, calls):
    """Voice adds a way to *ask*. It adds no way to authorise: the transcript
    route returns text and touches neither the ledger nor the gateway."""
    before = sum(1 for _ in web.LEDGER.entries())
    client.post("/api/voice/transcribe", content=b"pay me")
    assert sum(1 for _ in web.LEDGER.entries()) == before


def test_a_key_id_pasted_instead_of_a_key_says_so(monkeypatch, calls):
    """The dashboard shows a key ID beside the key. Pasting the ID gets a 400
    from ElevenLabs that reads like a wiring fault; this names the real fix."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "caad9511677f0480dc3fb146417bd0b2")
    with pytest.raises(voice.VoiceUnavailable, match="key ID, not a key"):
        voice.speak("hello", provider="elevenlabs")
    assert calls == [], "refused before it cost a request"


def test_surrounding_whitespace_in_the_key_is_forgiven(monkeypatch, calls):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "  sk_realkey  ")
    voice.speak("hello", provider="elevenlabs")
    assert calls[0]["headers"]["xi-api-key"] == "sk_realkey"


def test_the_speak_route_reports_which_service_spoke(client, key, calls):
    """The app has to know what it was handed: one service returns mp3 and the
    other wav, and a player told the wrong thing plays silence."""
    response = client.post("/api/voice/speak", json={"text": "hello"})
    assert response.status_code == 200
    assert response.headers["x-voice-provider"] == "elevenlabs"
    assert response.headers["content-type"] == "audio/mpeg"


def test_the_route_can_be_asked_for_either_service(client, monkeypatch, calls):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key")
    monkeypatch.setenv("RUMIK_API_KEY", "rk_live_test")
    rumik = client.post("/api/voice/speak", json={"text": "hello", "provider": "rumik"})
    assert rumik.headers["x-voice-provider"] == "rumik"
    assert rumik.headers["content-type"] == "audio/wav"


def test_an_unknown_provider_over_http_is_a_503_not_a_crash(client, key, calls):
    response = client.post("/api/voice/speak", json={"text": "x", "provider": "murf"})
    assert response.status_code == 503


def test_the_providers_route_lists_what_can_speak(client):
    body = client.get("/api/voice/providers").json()
    assert set(body["providers"]) == {"elevenlabs", "rumik"}
    assert body["default"] in body["providers"]
