#!/usr/bin/env python3
"""Kokoro via mlx-audio (Apple Silicon).

Runs inside the kokoro engine venv, either as a one-shot command or — as the
app uses it — as a long-lived process answering requests on stdin.
"""

from __future__ import annotations

import argparse

import numpy as np

from _common import run_cli_or_serve

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"


def load_model(request: dict):
    from mlx_audio.tts.utils import load_model as _load

    return _load(request.get("model") or DEFAULT_MODEL)


def generate(model, request: dict) -> tuple[np.ndarray, int]:
    chunks: list[np.ndarray] = []
    sample_rate = 24000
    for result in model.generate(
        text=request["text"],
        voice=request.get("voice") or "af_heart",
        speed=float(request.get("speed") or 1.0),
        lang_code=request.get("lang") or "a",
    ):
        chunks.append(np.asarray(result.audio).reshape(-1).astype(np.float32))
        sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
    if not chunks:
        raise RuntimeError("Kokoro produced no audio")

    wave = np.concatenate(chunks)
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > 1e-6:
        wave = wave * min(1.0, 0.95 / peak)
    return wave, sample_rate


def main() -> int:
    p = argparse.ArgumentParser(description="Kokoro TTS worker (MLX)")
    p.add_argument("--text")
    p.add_argument("--out")
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--lang", default="a", help="Kokoro lang_code: a=en-US, b=en-GB, …")
    p.add_argument("--model", default=DEFAULT_MODEL)
    return run_cli_or_serve(p, load_model, generate)


if __name__ == "__main__":
    raise SystemExit(main())
