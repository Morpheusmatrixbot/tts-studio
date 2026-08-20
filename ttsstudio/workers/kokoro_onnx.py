#!/usr/bin/env python3
"""Kokoro via ONNX Runtime (Windows / Linux / Intel Mac).

Same voices as the MLX backend, running on CPU. Weight files are downloaded by
the installer into TTS_STUDIO_MODEL_DIR.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import soundfile as sf

# Kokoro lang_code (shared with the MLX backend) → espeak language for ONNX.
LANG_MAP = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-br",
    "z": "cmn",
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--lang", default="a")
    args = p.parse_args()

    model_dir = Path(os.environ.get("TTS_STUDIO_MODEL_DIR", "."))
    onnx = model_dir / "kokoro-v1.0.onnx"
    voices = model_dir / "voices-v1.0.bin"
    for f in (onnx, voices):
        if not f.exists():
            raise SystemExit(f"Model file missing: {f}. Reinstall the Kokoro engine.")

    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(onnx), str(voices))
    lang = LANG_MAP.get(args.lang, args.lang if "-" in args.lang else "en-us")
    samples, sample_rate = kokoro.create(
        args.text, voice=args.voice, speed=float(args.speed), lang=lang
    )

    wave = np.asarray(samples, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > 1e-6:
        wave = wave * min(1.0, 0.95 / peak)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wave, int(sample_rate), subtype="PCM_16")
    print(f"OK {out.name} {len(wave) / int(sample_rate):.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
