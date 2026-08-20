#!/usr/bin/env python3
"""Chatterbox voice cloning via PyTorch (Windows / Linux / Intel Mac).

Uses CUDA when available, otherwise CPU. The multilingual checkpoint is loaded
only when a non-English language is requested, since it is the larger download.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from _common import postprocess, run_cli_or_serve


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(request: dict):
    device = pick_device()
    print(f"device={device}", flush=True)
    # The language of the first request fixes the checkpoint for the session;
    # the host starts one worker per job, and a job has one language.
    if (request.get("lang") or "en").lower() == "en":
        from chatterbox.tts import ChatterboxTTS

        return ChatterboxTTS.from_pretrained(device=device)
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    return ChatterboxMultilingualTTS.from_pretrained(device=device)


def generate(model, request: dict) -> tuple[np.ndarray, int]:
    ref = Path(request["ref_audio"]).resolve()
    if not ref.exists():
        raise RuntimeError(f"Voice sample missing: {ref}")

    lang = (request.get("lang") or "en").lower()
    kwargs = {
        "audio_prompt_path": str(ref),
        "temperature": float(request.get("temperature") or 0.55),
        "repetition_penalty": float(request.get("repetition_penalty") or 1.65),
        "exaggeration": float(request.get("exaggeration") or 0.5),
    }
    if lang != "en":
        kwargs["language_id"] = lang

    wav = model.generate(request["text"], **kwargs)
    sample_rate = int(getattr(model, "sr", 24000))
    array = wav.detach().cpu().numpy() if hasattr(wav, "detach") else wav
    wave = np.asarray(array, dtype=np.float32).reshape(-1)
    return postprocess(wave, sample_rate), sample_rate


def main() -> int:
    p = argparse.ArgumentParser(description="Chatterbox TTS worker (PyTorch)")
    p.add_argument("--text")
    p.add_argument("--out")
    p.add_argument("--ref-audio", dest="ref_audio")
    p.add_argument("--lang", default="en")
    p.add_argument("--temperature", type=float, default=0.55)
    p.add_argument("--repetition-penalty", dest="repetition_penalty", type=float, default=1.65)
    p.add_argument("--exaggeration", type=float, default=0.5)
    return run_cli_or_serve(p, load_model, generate)


if __name__ == "__main__":
    raise SystemExit(main())
