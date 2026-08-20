"""Voice catalogues and the user's own cloning samples.

Kokoro ships fixed presets, listed here with the language code each one needs.
Chatterbox has no presets at all — it clones whatever sample it is given, so its
"voices" are files the user drops into the app's samples folder.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

# Kokoro lang_code → human label. The same codes work on both backends.
KOKORO_LANGUAGES = {
    "a": "English (US)",
    "b": "English (UK)",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Portuguese (Brazil)",
    "z": "Chinese (Mandarin)",
}


def _v(vid: str, label: str, lang: str, gender: str) -> dict:
    return {"id": vid, "label": label, "lang": lang, "gender": gender}


KOKORO_VOICES: list[dict] = [
    _v("af_heart", "Heart", "a", "female"),
    _v("af_alloy", "Alloy", "a", "female"),
    _v("af_aoede", "Aoede", "a", "female"),
    _v("af_bella", "Bella", "a", "female"),
    _v("af_jessica", "Jessica", "a", "female"),
    _v("af_kore", "Kore", "a", "female"),
    _v("af_nicole", "Nicole", "a", "female"),
    _v("af_nova", "Nova", "a", "female"),
    _v("af_river", "River", "a", "female"),
    _v("af_sarah", "Sarah", "a", "female"),
    _v("af_sky", "Sky", "a", "female"),
    _v("am_adam", "Adam", "a", "male"),
    _v("am_echo", "Echo", "a", "male"),
    _v("am_eric", "Eric", "a", "male"),
    _v("am_fenrir", "Fenrir", "a", "male"),
    _v("am_liam", "Liam", "a", "male"),
    _v("am_michael", "Michael", "a", "male"),
    _v("am_onyx", "Onyx", "a", "male"),
    _v("am_puck", "Puck", "a", "male"),
    _v("am_santa", "Santa", "a", "male"),
    _v("bf_alice", "Alice", "b", "female"),
    _v("bf_emma", "Emma", "b", "female"),
    _v("bf_isabella", "Isabella", "b", "female"),
    _v("bf_lily", "Lily", "b", "female"),
    _v("bm_daniel", "Daniel", "b", "male"),
    _v("bm_fable", "Fable", "b", "male"),
    _v("bm_george", "George", "b", "male"),
    _v("bm_lewis", "Lewis", "b", "male"),
    _v("ef_dora", "Dora", "e", "female"),
    _v("em_alex", "Alex", "e", "male"),
    _v("ff_siwis", "Siwis", "f", "female"),
    _v("hf_alpha", "Alpha", "h", "female"),
    _v("hf_beta", "Beta", "h", "female"),
    _v("hm_omega", "Omega", "h", "male"),
    _v("hm_psi", "Psi", "h", "male"),
    _v("if_sara", "Sara", "i", "female"),
    _v("im_nicola", "Nicola", "i", "male"),
    _v("jf_alpha", "Alpha", "j", "female"),
    _v("jf_gongitsune", "Gongitsune", "j", "female"),
    _v("jf_nezumi", "Nezumi", "j", "female"),
    _v("jf_tebukuro", "Tebukuro", "j", "female"),
    _v("jm_kumo", "Kumo", "j", "male"),
    _v("pf_dora", "Dora", "p", "female"),
    _v("pm_alex", "Alex", "p", "male"),
    _v("zf_xiaobei", "Xiaobei", "z", "female"),
    _v("zf_xiaoni", "Xiaoni", "z", "female"),
    _v("zf_xiaoxiao", "Xiaoxiao", "z", "female"),
    _v("zf_xiaoyi", "Xiaoyi", "z", "female"),
    _v("zm_yunjian", "Yunjian", "z", "male"),
    _v("zm_yunxi", "Yunxi", "z", "male"),
    _v("zm_yunyang", "Yunyang", "z", "male"),
]

# Chatterbox multilingual checkpoint languages.
CHATTERBOX_LANGUAGES = {
    "en": "English", "ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek",
    "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch",
    "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ru": "Russian",
    "sv": "Swedish", "sw": "Swahili", "tr": "Turkish", "zh": "Chinese",
}

ELEVENLABS_MODELS = [
    {"id": "eleven_multilingual_v2", "label": "Multilingual v2 (best quality)"},
    {"id": "eleven_turbo_v2_5", "label": "Turbo v2.5 (faster, cheaper)"},
    {"id": "eleven_flash_v2_5", "label": "Flash v2.5 (fastest)"},
]

SAMPLE_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}


def samples_dir() -> Path:
    d = paths.data_dir() / "voice-samples"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_samples() -> list[dict]:
    out = []
    for p in sorted(samples_dir().glob("*.wav")):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        out.append(
            {"id": p.name, "label": p.stem.replace("_", " ").replace("-", " ").title(), "bytes": size}
        )
    return out


def sample_path(sample_id: str) -> Path:
    """Resolve a stored sample by name, refusing anything outside the folder."""
    name = Path(sample_id).name
    p = (samples_dir() / name).resolve()
    if p.parent != samples_dir().resolve():
        raise ValueError("Invalid voice sample")
    if not p.exists():
        raise FileNotFoundError(f"Voice sample not found: {name}")
    return p


def _slug(text: str) -> str:
    import re

    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return s or "voice"


def save_sample(raw: bytes, filename: str) -> dict:
    """Store an uploaded clip as mono 24 kHz WAV, which every backend accepts."""
    import io

    import numpy as np
    import soundfile as sf

    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in SAMPLE_SUFFIXES:
        raise ValueError(f"Unsupported audio type: {suffix}")

    dest = samples_dir() / f"{_slug(Path(filename).stem)}.wav"
    try:
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 — libsndfile cannot read m4a/aac
        converted = _convert_with_ffmpeg(raw, suffix)
        if converted is None:
            raise ValueError(
                "Could not read this audio file. Save it as WAV or MP3 and try again."
            ) from exc
        audio, sr = sf.read(io.BytesIO(converted), dtype="float32", always_2d=True)

    mono = audio.mean(axis=1).astype(np.float32)
    if sr != 24000 and mono.size:
        n = max(1, int(round(mono.size * 24000 / sr)))
        old = np.linspace(0.0, 1.0, num=mono.size, endpoint=False)
        new = np.linspace(0.0, 1.0, num=n, endpoint=False)
        mono = np.interp(new, old, mono).astype(np.float32)
        sr = 24000

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 1e-6:
        mono = mono * min(1.0, 0.95 / peak)

    seconds = mono.size / float(sr or 24000)
    if seconds < 1.0:
        raise ValueError("Voice sample is too short — use 5 to 20 seconds of clean speech.")

    sf.write(str(dest), mono, sr, subtype="PCM_16")
    return {
        "id": dest.name,
        "label": dest.stem.replace("_", " ").replace("-", " ").title(),
        "seconds": round(seconds, 1),
    }


def _convert_with_ffmpeg(raw: bytes, suffix: str) -> bytes | None:
    """Last resort for formats libsndfile will not open, when ffmpeg exists."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix or '.bin'}"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(raw)
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-ar", "24000", "-ac", "1", str(dst)],
            capture_output=True,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if proc.returncode != 0 or not dst.exists():
            return None
        return dst.read_bytes()


def delete_sample(sample_id: str) -> None:
    sample_path(sample_id).unlink(missing_ok=True)
