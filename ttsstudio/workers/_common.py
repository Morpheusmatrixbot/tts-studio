"""Shared machinery for the TTS workers: the serve loop and post-processing.

Workers run inside an engine's own virtualenv, so this module may only rely on
numpy and the standard library.

**Serve mode** is the reason narration is fast. Loading a model costs about four
seconds, and a novel is on the order of two thousand chunks; paying that per
chunk spends hours reloading weights that never changed. In serve mode the host
starts one worker, it loads the model once, and then it answers a stream of
requests over stdin/stdout until the job ends.

**Post-processing** fixes two artefacts autoregressive models produce often
enough to be worth handling at the source: near-silence before the first word,
and a tail that re-speaks the last clause. Both are cheap to spot on the
waveform and expensive to discover after a six-hour audiobook has rendered.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

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


# --------------------------------------------------------------------------
# Serve mode
# --------------------------------------------------------------------------

# One JSON object per line each way:
#   host → worker   {"text": "...", "out": "/path/file.wav", ...}
#                   {"cmd": "quit"}
#   worker → host   {"ready": true}                     once, after model load
#                   {"ok": true, "seconds": 12.3}       per request
#                   {"ok": false, "error": "..."}       per request
#
# Only these messages may reach stdout. Model libraries print banners and
# progress bars freely, so stdout is redirected to stderr for the duration and
# replies are written to the original handle.


def serve(load_model: Callable[[dict], object], generate: Callable[[object, dict], tuple]) -> int:
    """Answer synthesis requests on stdin until told to quit.

    ``load_model`` is called once with the first request, so the host can send
    model options (voice, checkpoint) without them being fixed at spawn time.
    ``generate`` returns ``(waveform, sample_rate)`` for one request.
    """
    import soundfile as sf

    protocol_out = sys.stdout
    sys.stdout = sys.stderr  # anything the model prints is diagnostics, not protocol

    def reply(payload: dict) -> None:
        protocol_out.write(json.dumps(payload) + "\n")
        protocol_out.flush()

    model = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            reply({"ok": False, "error": f"bad request: {exc}"})
            continue

        if request.get("cmd") == "quit":
            break

        try:
            if model is None:
                model = load_model(request)
                reply({"ready": True})

            wave, sample_rate = generate(model, request)
            out = Path(request["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(out), wave, sample_rate, subtype="PCM_16")
            reply({"ok": True, "seconds": round(len(wave) / sample_rate, 3)})
        except Exception as exc:  # noqa: BLE001 — one bad chunk must not kill the worker
            traceback.print_exc()
            reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return 0


def run_cli_or_serve(
    parser,
    load_model: Callable[[dict], object],
    generate: Callable[[object, dict], tuple],
) -> int:
    """Entry point shared by every worker.

    With ``--serve`` the worker becomes a long-lived process. Without it, it
    synthesises one file and exits — which keeps each worker independently
    runnable from a shell, the quickest way to debug an engine.
    """
    import soundfile as sf

    parser.add_argument("--serve", action="store_true", help="Answer requests on stdin")
    args = parser.parse_args()

    if args.serve:
        return serve(load_model, generate)

    request = {k: v for k, v in vars(args).items() if v is not None}
    model = load_model(request)
    wave, sample_rate = generate(model, request)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wave, sample_rate, subtype="PCM_16")
    print(f"OK {out.name} {len(wave) / sample_rate:.2f}s", flush=True)
    return 0
