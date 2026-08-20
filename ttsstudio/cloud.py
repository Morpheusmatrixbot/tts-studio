"""Cloud engines: Edge TTS (free) and ElevenLabs (bring your own key)."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import settings

ELEVENLABS_KEYS_URL = "https://elevenlabs.io/app/settings/api-keys"

_EDGE_CACHE: list[dict] | None = None
_EDGE_LOCK = threading.Lock()

# Used when the voice list cannot be fetched (offline at startup).
_EDGE_FALLBACK = [
    {"id": "en-US-GuyNeural", "label": "en-US-GuyNeural (Male)", "locale": "en-US", "gender": "Male"},
    {"id": "en-US-JennyNeural", "label": "en-US-JennyNeural (Female)", "locale": "en-US", "gender": "Female"},
    {"id": "en-GB-RyanNeural", "label": "en-GB-RyanNeural (Male)", "locale": "en-GB", "gender": "Male"},
    {"id": "ru-RU-DmitryNeural", "label": "ru-RU-DmitryNeural (Male)", "locale": "ru-RU", "gender": "Male"},
    {"id": "ru-RU-SvetlanaNeural", "label": "ru-RU-SvetlanaNeural (Female)", "locale": "ru-RU", "gender": "Female"},
]


def edge_voices(refresh: bool = False) -> list[dict]:
    global _EDGE_CACHE
    with _EDGE_LOCK:
        if _EDGE_CACHE is not None and not refresh:
            return _EDGE_CACHE
        try:
            import edge_tts

            raw = asyncio.run(edge_tts.list_voices())
            voices = [
                {
                    "id": v["ShortName"],
                    "label": f'{v["ShortName"]} ({v["Gender"]})',
                    "locale": v["Locale"],
                    "gender": v["Gender"],
                }
                for v in raw
            ]
            voices.sort(key=lambda v: (v["locale"], v["id"]))
            _EDGE_CACHE = voices
        except Exception:  # noqa: BLE001 — offline is not fatal, the app still runs
            _EDGE_CACHE = list(_EDGE_FALLBACK)
        return _EDGE_CACHE


async def _edge_speak(text: str, voice: str, out: Path, rate: str, pitch: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                fh.write(chunk["data"])


def synth_edge(text: str, out: Path, *, voice: str, rate: str = "+0%", pitch: str = "+0Hz") -> Path:
    asyncio.run(_edge_speak(text, voice, out, rate, pitch))
    if not out.exists() or out.stat().st_size < 100:
        raise RuntimeError("Edge TTS returned no audio (check your internet connection)")
    return out


def elevenlabs_voices() -> list[dict]:
    key = settings.elevenlabs_key()
    if not key:
        return []
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ElevenLabs rejected the API key (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach ElevenLabs: {exc.reason}") from exc
    return [
        {"id": v.get("voice_id"), "label": v.get("name") or v.get("voice_id")}
        for v in data.get("voices", [])
    ]


def synth_elevenlabs(
    text: str,
    out: Path,
    *,
    voice: str,
    model_id: str = "eleven_multilingual_v2",
    stability: float = 0.45,
    similarity_boost: float = 0.8,
    style: float = 0.2,
    speed: float = 1.0,
) -> Path:
    key = settings.elevenlabs_key()
    if not key:
        raise RuntimeError("No ElevenLabs API key set. Add one in Settings.")
    if not voice:
        raise RuntimeError("Pick an ElevenLabs voice first.")

    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": float(stability),
            "similarity_boost": float(similarity_boost),
            "style": float(style),
            # The API documents 0.7–1.2; anything outside degrades quality.
            "speed": float(min(1.2, max(0.7, speed))),
            "use_speaker_boost": True,
        },
    }
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"ElevenLabs HTTP {exc.code}: {detail}") from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    if out.stat().st_size < 100:
        raise RuntimeError("ElevenLabs returned empty audio")
    return out


def verify_elevenlabs_key(key: str) -> dict:
    """Check a key before saving it, so a typo surfaces immediately."""
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key.strip()}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code} — key rejected"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"Network error: {exc.reason}"}
    used = data.get("character_count")
    limit = data.get("character_limit")
    return {
        "ok": True,
        "tier": data.get("tier", "unknown"),
        "characters_used": used,
        "characters_limit": limit,
    }
