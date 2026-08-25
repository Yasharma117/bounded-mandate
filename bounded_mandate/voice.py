"""The one place that hears and speaks.

Mirrors `razorpay_gateway`: the ElevenLabs key lives in this process and never
reaches the app. The phone sends audio and receives audio; it never holds a
credential, so a decompiled bundle yields nothing.

Both calls are synchronous. FastAPI runs sync handlers in a threadpool, and a
short recording round-trips faster than the ceremony of async would save.
"""

from __future__ import annotations

import os

import httpx2 as httpx

BASE_URL = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1")
STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
# Sarah — a *premade* voice, which matters: free accounts cannot use library
# voices over the API and get a 402 that looks like a billing fault rather than
# a voice-id one. Premade voices work on every tier. `GET /v1/voices` lists what
# an account can actually reach; swap with the env var.
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
TIMEOUT = 30.0

# 4 MB of m4a is several minutes of speech. Past that something is wrong, and
# the boundary refuses rather than paying to find out.
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_SPEAK_CHARS = 2_000


class VoiceUnavailable(RuntimeError):
    """No key configured, or the provider refused. Voice is optional; typing is not."""


def _headers() -> dict[str, str]:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise VoiceUnavailable("ELEVENLABS_API_KEY is not configured")
    # The dashboard shows a key *ID* beside the key, and pasting the ID gets a
    # 400 from ElevenLabs that reads like a wiring problem rather than a typo.
    # Catch it here, where the message can name the fix.
    if not key.startswith("sk_"):
        raise VoiceUnavailable(
            "ELEVENLABS_API_KEY looks like a key ID, not a key. Keys start with "
            "'sk_' and are shown only once, when the key is created — create a "
            "new one if it was not saved."
        )
    return {"xi-api-key": key}


def _check(response: httpx.Response) -> None:
    if response.status_code >= 400:
        detail = response.text[:200] if response.content else response.reason_phrase
        raise VoiceUnavailable(f"elevenlabs {response.status_code}: {detail}")


def transcribe(audio: bytes, *, filename: str = "speech.m4a") -> str:
    """Spoken audio to text. The text is a *proposal to the agent*, never a command
    to the engine — a voice channel widens what an attacker can say, not what
    the engine will authorise."""
    if not audio:
        raise VoiceUnavailable("no audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceUnavailable(f"audio exceeds {MAX_AUDIO_BYTES} bytes")
    try:
        response = httpx.post(
            f"{BASE_URL}/speech-to-text",
            headers=_headers(),
            files={"file": (filename, audio, "audio/m4a")},
            data={"model_id": STT_MODEL},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"speech-to-text unreachable: {exc}") from exc
    _check(response)
    return response.json().get("text", "").strip()


def speak(text: str) -> bytes:
    """Text to mp3 bytes."""
    text = text.strip()
    if not text:
        raise VoiceUnavailable("nothing to say")
    try:
        response = httpx.post(
            f"{BASE_URL}/text-to-speech/{VOICE_ID}",
            headers={**_headers(), "Content-Type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={"text": text[:MAX_SPEAK_CHARS], "model_id": TTS_MODEL},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"text-to-speech unreachable: {exc}") from exc
    _check(response)
    return response.content
