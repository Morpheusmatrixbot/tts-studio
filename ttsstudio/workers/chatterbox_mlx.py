#!/usr/bin/env python3
"""Chatterbox voice cloning via mlx-audio (Apple Silicon)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

from _common import postprocess


def max_tokens_for(text: str) -> int:
    """Speech-token budget. Too tight and the model cuts a sentence off, then repeats it."""
    words = max(1, len(text.split()))
    return int(max(480, min(1400, words * 16 + 64)))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ref-audio", required=True, help="Voice sample to clone")
    p.add_argument("--model", default="mlx-community/chatterbox-turbo-fp16")
    p.add_argument("--lang", default="en")
    p.add_argument("--temperature", type=float, default=0.55)
    p.add_argument("--repetition-penalty", type=float, default=1.65)
    args = p.parse_args()

    ref = Path(args.ref_audio).resolve()
    if not ref.exists():
        raise SystemExit(f"Voice sample missing: {ref}")

    from mlx_audio.tts.utils import load_model

    model = load_model(args.model)
    chunks: list[np.ndarray] = []
    sr = 24000
    for result in model.generate(
        text=args.text,
        ref_audio=str(ref),
        lang_code=args.lang,
        verbose=False,
        temperature=float(args.temperature),
        repetition_penalty=float(args.repetition_penalty),
        max_tokens=max_tokens_for(args.text),
    ):
        audio = getattr(result, "audio", None)
        if audio is None:
            continue
        chunks.append(np.asarray(audio).reshape(-1).astype(np.float32))
        sr = int(getattr(result, "sample_rate", sr) or sr)
    if not chunks:
        raise SystemExit("Chatterbox produced no audio")

    wave = postprocess(np.concatenate(chunks), sr)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wave, sr, subtype="PCM_16")
    print(f"OK {out.name} {len(wave) / sr:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
