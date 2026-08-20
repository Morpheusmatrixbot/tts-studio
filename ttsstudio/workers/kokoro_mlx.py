#!/usr/bin/env python3
"""Kokoro via mlx-audio (Apple Silicon). Runs inside the kokoro engine venv."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--lang", default="a", help="Kokoro lang_code: a=en-US, b=en-GB, …")
    p.add_argument("--model", default="mlx-community/Kokoro-82M-bf16")
    args = p.parse_args()

    from mlx_audio.tts.utils import load_model

    model = load_model(args.model)
    chunks: list[np.ndarray] = []
    sample_rate = 24000
    for result in model.generate(
        text=args.text, voice=args.voice, speed=float(args.speed), lang_code=args.lang
    ):
        chunks.append(np.asarray(result.audio).reshape(-1).astype(np.float32))
        sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
    if not chunks:
        raise SystemExit("Kokoro produced no audio")

    wave = np.concatenate(chunks)
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > 1e-6:
        wave = wave * min(1.0, 0.95 / peak)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wave, sample_rate, subtype="PCM_16")
    print(f"OK {out.name} {len(wave) / sample_rate:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
