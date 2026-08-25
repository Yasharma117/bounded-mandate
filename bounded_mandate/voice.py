"""The one place that hears and speaks.

Mirrors `razorpay_gateway`: the provider keys live in this process and never
reach the app. The phone sends audio and receives audio; it never holds a
credential, so a decompiled bundle yields nothing.

**Hearing is ElevenLabs; speaking is either.** Scribe is the only transcriber
here, but two services synthesise speech and they are swapped with one
environment variable — so the choice can be made by ear, on the recorded demo,
rather than argued about now.

Both calls are synchronous. FastAPI runs sync handlers in a threadpool, and a
short recording round-trips faster than the ceremony of async would save.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx2 as httpx

TIMEOUT = 30.0

# 4 MB of m4a is several minutes of speech. Past that something is wrong, and
# the boundary refuses rather than paying to find out.
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_SPEAK_CHARS = 2_000

# --- hearing: ElevenLabs Scribe ---------------------------------------------

ELEVENLABS_URL = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1")
STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")

# --- speaking ---------------------------------------------------------------

# Which service says it. `elevenlabs` or `rumik`; one line, so the demo can be
# recorded with whichever sounds right.
TTS_PROVIDER = os.environ.get("BM_TTS_PROVIDER", "elevenlabs").strip().lower()

TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
# Sarah — a *premade* voice, which matters: free accounts cannot use library
# voices over the API and get a 402 that looks like a billing fault rather than
# a voice-id one. Premade voices work on every tier. `GET /v1/voices` lists what
# an account can actually reach.
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")

RUMIK_URL = os.environ.get("RUMIK_BASE_URL", "https://silk-api.rumik.ai/v1")
RUMIK_MODEL = os.environ.get("RUMIK_MODEL", "mulberry")
# An Indian-English voice, because this product talks about rupees, Instamart
# and atta. `muga` steers with tone tags; `mulberry` with a description.
RUMIK_SPEAKER = os.environ.get("RUMIK_SPEAKER", "siya")
RUMIK_DESCRIPTION = os.environ.get("RUMIK_DESCRIPTION", "calm, clear, reassuring Indian English")


class VoiceUnavailable(RuntimeError):
    """No key configured, or the provider refused. Voice is optional; typing is not."""


@dataclass(frozen=True)
class Speech:
    """Synthesised audio, and what it is — the two services disagree on format
    and the player has to be told which it got."""

    audio: bytes
    media_type: str
    provider: str


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise VoiceUnavailable(f"{name} is not configured")
    return value


def _elevenlabs_key() -> str:
    key = _key("ELEVENLABS_API_KEY")
    # The dashboard shows a key *ID* beside the key, and pasting the ID gets a
    # 400 from ElevenLabs that reads like a wiring problem rather than a typo.
    # Catch it here, where the message can name the fix.
    if not key.startswith("sk_"):
        raise VoiceUnavailable(
            "ELEVENLABS_API_KEY looks like a key ID, not a key. Keys start with "
            "'sk_' and are shown only once, when the key is created — create a "
            "new one if it was not saved."
        )
    return key


def _check(response: httpx.Response, provider: str) -> None:
    if response.status_code >= 400:
        detail = response.text[:200] if response.content else response.reason_phrase
        raise VoiceUnavailable(f"{provider} {response.status_code}: {detail}")


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
            f"{ELEVENLABS_URL}/speech-to-text",
            headers={"xi-api-key": _elevenlabs_key()},
            files={"file": (filename, audio, "audio/m4a")},
            data={"model_id": STT_MODEL},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"speech-to-text unreachable: {exc}") from exc
    _check(response, "elevenlabs")
    return response.json().get("text", "").strip()


def _speak_elevenlabs(text: str) -> Speech:
    try:
        response = httpx.post(
            f"{ELEVENLABS_URL}/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": _elevenlabs_key(), "Content-Type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": TTS_MODEL},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"text-to-speech unreachable: {exc}") from exc
    _check(response, "elevenlabs")
    return Speech(response.content, "audio/mpeg", "elevenlabs")


def _speak_rumik(text: str) -> Speech:
    body: dict[str, object] = {"text": text, "model": RUMIK_MODEL}
    # `mulberry` is steered by a description and an optional preset speaker;
    # `muga` takes neither, and sending them is a 4xx rather than a no-op.
    if RUMIK_MODEL == "mulberry":
        body["description"] = RUMIK_DESCRIPTION
        if RUMIK_SPEAKER:
            body["speaker"] = RUMIK_SPEAKER
    try:
        response = httpx.post(
            f"{RUMIK_URL}/tts",
            headers={
                "Authorization": f"Bearer {_key('RUMIK_API_KEY')}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"text-to-speech unreachable: {exc}") from exc
    _check(response, "rumik")
    return Speech(response.content, "audio/wav", "rumik")


SPEAKERS = {"elevenlabs": _speak_elevenlabs, "rumik": _speak_rumik}


def speak(text: str, *, provider: str | None = None) -> Speech:
    """Text to audio, from whichever service is configured.

    `provider` overrides the default for one call, which is how both can be
    compared on the same sentence without restarting anything.
    """
    text = text.strip()
    if not text:
        raise VoiceUnavailable("nothing to say")
    chosen = (provider or TTS_PROVIDER).strip().lower()
    synthesise = SPEAKERS.get(chosen)
    if synthesise is None:
        known = ", ".join(sorted(SPEAKERS))
        raise VoiceUnavailable(f"no such voice provider: {chosen}. Providers are: {known}")
    return synthesise(text[:MAX_SPEAK_CHARS])
