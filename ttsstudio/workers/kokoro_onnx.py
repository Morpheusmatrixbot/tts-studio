#!/usr/bin/env python3
"""Kokoro via ONNX Runtime (Windows / Linux / Intel Mac).

Same voices as the MLX backend, running on CPU. The weight files are fetched by
the installer into TTS_STUDIO_MODEL_DIR.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np

from _common import run_cli_or_serve

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


def load_model(request: dict):
    from kokoro_onnx import Kokoro

    model_dir = Path(os.environ.get("TTS_STUDIO_MODEL_DIR", "."))
    onnx = model_dir / "kokoro-v1.0.onnx"
    voices = model_dir / "voices-v1.0.bin"
    for path in (onnx, voices):
        if not path.exists():
            raise RuntimeError(f"Model file missing: {path}. Reinstall the Kokoro engine.")
    return Kokoro(str(onnx), str(voices))


def generate(model, request: dict) -> tuple[np.ndarray, int]:
    lang_code = request.get("lang") or "a"
    lang = LANG_MAP.get(lang_code, lang_code if "-" in lang_code else "en-us")
    samples, sample_rate = model.create(
        request["text"],
        voice=request.get("voice") or "af_heart",
        speed=float(request.get("speed") or 1.0),
        lang=lang,
    )

    wave = np.asarray(samples, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > 1e-6:
        wave = wave * min(1.0, 0.95 / peak)
    return wave, int(sample_rate)


def main() -> int:
    p = argparse.ArgumentParser(description="Kokoro TTS worker (ONNX Runtime)")
    p.add_argument("--text")
    p.add_argument("--out")
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--lang", default="a")
    return run_cli_or_serve(p, load_model, generate)


if __name__ == "__main__":
    raise SystemExit(main())
