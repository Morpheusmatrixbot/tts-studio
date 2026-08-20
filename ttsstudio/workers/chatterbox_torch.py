#!/usr/bin/env python3
"""Chatterbox voice cloning via PyTorch (Windows / Linux / Intel Mac).

Uses CUDA when available, otherwise CPU. The multilingual checkpoint is loaded
only when a non-English language is requested, since it is the larger download.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

from _common import postprocess


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ref-audio", required=True)
    p.add_argument("--lang", default="en")
    p.add_argument("--temperature", type=float, default=0.55)
    p.add_argument("--repetition-penalty", type=float, default=1.65)
    p.add_argument("--exaggeration", type=float, default=0.5)
    args = p.parse_args()

    ref = Path(args.ref_audio).resolve()
    if not ref.exists():
        raise SystemExit(f"Voice sample missing: {ref}")

    device = pick_device()
    print(f"device={device}", flush=True)
    lang = (args.lang or "en").lower()

    if lang == "en":
        from chatterbox.tts import ChatterboxTTS

        model = ChatterboxTTS.from_pretrained(device=device)
        wav = model.generate(
            args.text,
            audio_prompt_path=str(ref),
            temperature=float(args.temperature),
            repetition_penalty=float(args.repetition_penalty),
            exaggeration=float(args.exaggeration),
        )
    else:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        wav = model.generate(
            args.text,
            language_id=lang,
            audio_prompt_path=str(ref),
            temperature=float(args.temperature),
            repetition_penalty=float(args.repetition_penalty),
            exaggeration=float(args.exaggeration),
        )

    sr = int(getattr(model, "sr", 24000))
    wave = np.asarray(wav.detach().cpu().numpy() if hasattr(wav, "detach") else wav, dtype=np.float32).reshape(-1)
    wave = postprocess(wave, sr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wave, sr, subtype="PCM_16")
    print(f"OK {out.name} {len(wave) / sr:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
