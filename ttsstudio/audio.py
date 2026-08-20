"""Chunking, stitching and export.

Neural TTS models degrade on long inputs — Chatterbox in particular starts
dissolving into syllables past roughly 90 words — so text is synthesised in
sentence-sized pieces and joined back together here, with the silence between
them chosen to match the punctuation that produced the break.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import soundfile as sf

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Gap inserted between pieces, in milliseconds.
GAP_SENTENCE = 180.0
GAP_PARAGRAPH = 420.0
GAP_SECTION = 900.0


def clean(text: str) -> str:
    t = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # An unclosed quote makes autoregressive models restart the clause.
    if t.count('"') % 2 == 1:
        t = t.rstrip() + '"'
    return re.sub(r"\s+", " ", t).strip()


def _split_long_sentence(sentence: str, max_words: int) -> list[str]:
    """Break a runaway sentence on inner punctuation so no piece exceeds the cap."""
    words = sentence.split()
    if len(words) <= max_words:
        return [sentence]
    parts = [p.strip() for p in re.split(r"(?<=[;:,—])\s+", sentence) if p.strip()]
    out: list[str] = []
    buf: list[str] = []
    for part in parts:
        buf.append(part)
        if len(" ".join(buf).split()) >= max_words:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    # Still too long (no inner punctuation at all) — fall back to a hard split.
    final: list[str] = []
    for piece in out or [sentence]:
        pw = piece.split()
        if len(pw) <= max_words * 1.5:
            final.append(piece)
        else:
            for i in range(0, len(pw), max_words):
                final.append(" ".join(pw[i : i + max_words]))
    return final


def chunk_paragraphs(paragraphs: list[str], max_words: int = 55) -> list[tuple[str, bool]]:
    """Pack sentences up to max_words. The flag marks a paragraph boundary."""
    out: list[tuple[str, bool]] = []
    buf: list[str] = []
    buf_words = 0
    buf_break = True

    def flush() -> None:
        nonlocal buf, buf_words, buf_break
        text = clean(" ".join(buf))
        if text:
            out.append((text, buf_break))
        buf, buf_words, buf_break = [], 0, False

    for para in paragraphs:
        cleaned = clean(para)
        if not cleaned:
            continue
        sentences: list[str] = []
        for raw in _SENTENCE_SPLIT.split(cleaned):
            raw = raw.strip()
            if raw:
                sentences.extend(_split_long_sentence(raw, max_words))
        at_paragraph_start = True
        for sentence in sentences:
            sw = max(1, len(sentence.split()))
            if buf and buf_words + sw > max_words:
                flush()
                buf_break = at_paragraph_start
            buf.append(sentence)
            buf_words += sw
            at_paragraph_start = False
        if buf and buf_words >= max_words - 8:
            flush()
            buf_break = True
    if buf:
        flush()
    return out


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return audio.mean(axis=1).astype(np.float32), int(sr)


def resample(audio: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Linear resample — only ever used to reconcile two backends' rates."""
    if sr_from == sr_to or audio.size == 0:
        return audio
    n = max(1, int(round(audio.size * sr_to / sr_from)))
    old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(new, old, audio).astype(np.float32)


def concat(paths: list[Path], gaps_ms: list[float]) -> tuple[np.ndarray, int]:
    """Join audio files, inserting gaps_ms[i] of silence *before* file i."""
    target_sr: int | None = None
    parts: list[np.ndarray] = []
    for i, path in enumerate(paths):
        audio, sr = read_mono(path)
        if target_sr is None:
            target_sr = sr
        elif sr != target_sr:
            audio = resample(audio, sr, target_sr)
        if i > 0 and gaps_ms[i] > 0:
            parts.append(np.zeros(int(target_sr * gaps_ms[i] / 1000.0), dtype=np.float32))
        parts.append(audio)
    sr = target_sr or 24000
    return (np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)), sr


def write(path: Path, audio: np.ndarray, sr: int, *, fmt: str | None = None) -> Path:
    """Write WAV or MP3. libsndfile ≥1.1 encodes MP3, so no ffmpeg is needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1e-6:
        audio = audio * min(1.0, 0.95 / peak)
    audio = np.clip(audio, -1.0, 1.0)
    suffix = path.suffix.lower()
    fmt = fmt or ("MP3" if suffix == ".mp3" else "WAV")
    if fmt == "MP3":
        sf.write(str(path), audio, sr, format="MP3")
    else:
        sf.write(str(path), audio, sr, subtype="PCM_16")
    return path


def mp3_supported() -> bool:
    try:
        return "MP3" in sf.available_formats()
    except Exception:  # noqa: BLE001
        return False


def duration_of(path: Path) -> float:
    try:
        info = sf.info(str(path))
        return float(info.frames) / float(info.samplerate)
    except Exception:  # noqa: BLE001
        return 0.0
