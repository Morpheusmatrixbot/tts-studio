"""Shared post-processing for autoregressive TTS workers.

Chatterbox generates token by token, and two artefacts show up often enough to
be worth fixing at the source: a stretch of near-silence before the first word,
and a tail that re-speaks the last clause. Both are cheap to detect on the
waveform and expensive to notice only after a six-hour audiobook has rendered.

Imported by the worker scripts, which run inside an engine venv — so this may
only rely on numpy.
"""

from __future__ import annotations

import numpy as np


def trim_leading_silence(
    wave: np.ndarray, sr: int, *, pad_ms: float = 40.0, threshold_db: float = -45.0
) -> np.ndarray:
    """Cut dead air before the first word down to a short pad."""
    if wave.size == 0:
        return wave
    win = max(1, int(sr * 0.01))
    n_win = wave.size // win
    if n_win == 0:
        return wave
    frames = wave[: n_win * win].reshape(n_win, win)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    voiced = np.where(db > threshold_db)[0]
    if voiced.size == 0:
        return wave
    cut = max(0, voiced[0] * win - int(sr * pad_ms / 1000))
    return wave[cut:]


def trim_repeating_tail(
    wave: np.ndarray,
    sr: int,
    *,
    min_tail_s: float = 1.05,
    max_probe_s: float = 4.0,
    corr_thresh: float = 0.84,
    max_cuts: int = 2,
) -> tuple[np.ndarray, int]:
    """Drop a trailing window that closely repeats the window just before it.

    Compares energy envelopes rather than raw samples, which tolerates the
    slight pitch drift between a phrase and its accidental re-utterance.
    """
    if wave.size < int(sr * (min_tail_s * 2 + 0.2)):
        return wave, 0

    cuts = 0
    hop = max(1, int(sr * 0.02))
    cur = wave
    while cuts < max_cuts:
        max_win = min(int(sr * max_probe_s), cur.size // 2)
        min_win = int(sr * min_tail_s)
        if max_win < min_win:
            break
        best_corr, best_win = -1.0, 0
        for win in range(min_win, max_win + 1, hop):
            a, b = cur[-(2 * win) : -win], cur[-win:]
            if a.size != b.size or a.size < hop * 4:
                continue
            n = (a.size // hop) * hop
            if n < hop * 4:
                continue
            a_e = np.sqrt(np.mean(a[:n].reshape(-1, hop) ** 2, axis=1) + 1e-12)
            b_e = np.sqrt(np.mean(b[:n].reshape(-1, hop) ** 2, axis=1) + 1e-12)
            if a_e.std() < 1e-5 or b_e.std() < 1e-5:
                continue
            corr = float(np.corrcoef(a_e, b_e)[0, 1])
            if corr > best_corr:
                best_corr, best_win = corr, win
        if best_win <= 0 or best_corr < corr_thresh:
            break
        cur = cur[:-best_win]
        cuts += 1
    return cur, cuts


def normalize(wave: np.ndarray, peak_target: float = 0.92) -> np.ndarray:
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak <= 1e-6:
        return wave
    return wave * min(1.0, peak_target / peak)


def postprocess(wave: np.ndarray, sr: int, *, trim_tail: bool = True) -> np.ndarray:
    wave = trim_leading_silence(np.asarray(wave, dtype=np.float32).reshape(-1), sr)
    if trim_tail:
        wave, n = trim_repeating_tail(wave, sr)
        if n:
            print(f"trimmed {n} repeating tail window(s)", flush=True)
    return normalize(wave)
