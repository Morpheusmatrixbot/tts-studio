#!/usr/bin/env python3
"""Chatterbox voice cloning via mlx-audio (Apple Silicon)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from _common import postprocess, run_cli_or_serve

DEFAULT_MODEL = "mlx-community/chatterbox-turbo-fp16"


def max_tokens_for(text: str) -> int:
    """Speech-token budget. Too tight and the model cuts a sentence off, then repeats it."""
    words = max(1, len(text.split()))
    return int(max(480, min(1400, words * 16 + 64)))


def load_model(request: dict):
    from mlx_audio.tts.utils import load_model as _load

    return _load(request.get("model") or DEFAULT_MODEL)


def generate(model, request: dict) -> tuple[np.ndarray, int]:
    ref = Path(request["ref_audio"]).resolve()
    if not ref.exists():
        raise RuntimeError(f"Voice sample missing: {ref}")

    text = request["text"]
    chunks: list[np.ndarray] = []
    sample_rate = 24000
    for result in model.generate(
        text=text,
        ref_audio=str(ref),
        lang_code=request.get("lang") or "en",
        verbose=False,
        temperature=float(request.get("temperature") or 0.55),
        repetition_penalty=float(request.get("repetition_penalty") or 1.65),
        max_tokens=max_tokens_for(text),
    ):
        audio = getattr(result, "audio", None)
        if audio is None:
            continue
        chunks.append(np.asarray(audio).reshape(-1).astype(np.float32))
        sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
    if not chunks:
        raise RuntimeError("Chatterbox produced no audio")

    return postprocess(np.concatenate(chunks), sample_rate), sample_rate


def main() -> int:
    p = argparse.ArgumentParser(description="Chatterbox TTS worker (MLX)")
    p.add_argument("--text")
    p.add_argument("--out")
    p.add_argument("--ref-audio", dest="ref_audio", help="Voice sample to clone")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--lang", default="en")
    p.add_argument("--temperature", type=float, default=0.55)
    p.add_argument("--repetition-penalty", dest="repetition_penalty", type=float, default=1.65)
    return run_cli_or_serve(p, load_model, generate)


if __name__ == "__main__":
    raise SystemExit(main())
