"""Dispatch one chunk of text to whichever engine the user picked.

Local engines run as a subprocess against their own virtualenv — that isolation
is what lets MLX, PyTorch and ONNX coexist on one machine. Cloud engines are
plain HTTP from this process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import cloud, runtime, voices
from .engines import backend_for

# Model repositories per engine/backend.
KOKORO_MLX_MODEL = "mlx-community/Kokoro-82M-bf16"
CHATTERBOX_MLX_MODEL = "mlx-community/chatterbox-turbo-fp16"
CHATTERBOX_MLX_MULTILINGUAL = "mlx-community/chatterbox-multilingual-v3"


class SynthError(RuntimeError):
    pass


def output_suffix(engine_id: str) -> str:
    """Cloud engines hand back MP3; local workers write WAV."""
    return ".mp3" if engine_id in {"edge", "elevenlabs"} else ".wav"


def _run_worker(engine_id: str, args: list[str], *, timeout: int = 1800) -> None:
    python = runtime.engine_python(engine_id)
    if not python.exists():
        raise SynthError(f"{engine_id} is not installed yet. Open the Engines tab and install it.")
    script = runtime.worker_script(engine_id)
    if not script.exists():
        raise SynthError(f"Worker script missing: {script}")

    cmd = [str(python), str(script), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=runtime.worker_env(engine_id),
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise SynthError(f"{engine_id} timed out after {timeout}s on one chunk") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = "\n".join(detail[-8:]) if detail else "no output"
        raise SynthError(f"{engine_id} failed:\n{tail}")


def synth_chunk(text: str, out_path: Path, cfg: dict) -> Path:
    """Render one chunk. Returns the file actually written."""
    engine = cfg["engine"]
    out_path = out_path.with_suffix(output_suffix(engine))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if engine == "edge":
        cloud.synth_edge(
            text,
            out_path,
            voice=cfg.get("voice") or "en-US-GuyNeural",
            rate=cfg.get("rate", "+0%"),
            pitch=cfg.get("pitch", "+0Hz"),
        )
        return out_path

    if engine == "elevenlabs":
        cloud.synth_elevenlabs(
            text,
            out_path,
            voice=cfg.get("voice", ""),
            model_id=cfg.get("model_id") or "eleven_multilingual_v2",
            speed=float(cfg.get("speed") or 1.0),
        )
        return out_path

    if engine == "kokoro":
        backend = backend_for("kokoro")
        args = [
            "--text", text,
            "--out", str(out_path),
            "--voice", cfg.get("voice") or "af_heart",
            "--speed", str(float(cfg.get("speed") or 1.0)),
            "--lang", cfg.get("lang") or "a",
        ]
        if backend and backend.id == "mlx":
            args += ["--model", cfg.get("model_id") or KOKORO_MLX_MODEL]
        _run_worker("kokoro", args)
        return out_path

    if engine == "chatterbox":
        sample_id = cfg.get("voice_sample")
        if not sample_id:
            raise SynthError("Chatterbox needs a voice sample. Upload one in the Voice panel.")
        ref = voices.sample_path(sample_id)
        lang = (cfg.get("lang") or "en").lower()
        args = [
            "--text", text,
            "--out", str(out_path),
            "--ref-audio", str(ref),
            "--lang", lang,
            "--temperature", str(float(cfg.get("temperature") or 0.55)),
            "--repetition-penalty", str(float(cfg.get("repetition_penalty") or 1.65)),
        ]
        backend = backend_for("chatterbox")
        if backend and backend.id == "mlx":
            model = cfg.get("model_id")
            if not model:
                model = CHATTERBOX_MLX_MODEL if lang == "en" else CHATTERBOX_MLX_MULTILINGUAL
            args += ["--model", model]
        _run_worker("chatterbox", args)
        return out_path

    raise SynthError(f"Unknown engine: {engine}")


def preflight(cfg: dict) -> None:
    """Fail fast, before a long book starts, on anything we can check up front."""
    engine = cfg.get("engine")
    if engine in {"kokoro", "chatterbox"}:
        if not runtime.is_installed(engine):
            raise SynthError(f"{engine} is not installed yet. Open the Engines tab and install it.")
    if engine == "chatterbox":
        sample_id = cfg.get("voice_sample")
        if not sample_id:
            raise SynthError("Chatterbox needs a voice sample.")
        voices.sample_path(sample_id)  # raises if missing
    if engine == "elevenlabs":
        from . import settings

        if not settings.elevenlabs_key():
            raise SynthError("No ElevenLabs API key set. Add one in Settings.")
        if not cfg.get("voice"):
            raise SynthError("Pick an ElevenLabs voice first.")
    if engine == "edge" and not cfg.get("voice"):
        raise SynthError("Pick an Edge TTS voice first.")
