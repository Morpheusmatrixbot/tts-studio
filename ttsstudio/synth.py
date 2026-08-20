"""Turning configuration into audio, one chunk at a time.

A narration opens a session, feeds it chunks, and closes it. Local engines back
that session with one long-lived subprocess against their own virtualenv — the
isolation is what lets MLX, PyTorch and ONNX coexist, and the longevity is what
stops the model being reloaded for every chunk. Cloud engines are stateless HTTP
and ignore the session entirely.
"""

from __future__ import annotations

from pathlib import Path

from . import cloud, runtime, voices
from .engines import backend_for
from .worker_pool import EngineWorker, WorkerError

KOKORO_MLX_MODEL = "mlx-community/Kokoro-82M-bf16"
CHATTERBOX_MLX_MODEL = "mlx-community/chatterbox-turbo-fp16"
CHATTERBOX_MLX_MULTILINGUAL = "mlx-community/chatterbox-multilingual-v3"

LOCAL_ENGINES = {"kokoro", "chatterbox"}
CLOUD_ENGINES = {"edge", "elevenlabs"}


class SynthError(RuntimeError):
    pass


def output_suffix(engine_id: str) -> str:
    """Cloud engines hand back MP3; local workers write WAV."""
    return ".mp3" if engine_id in CLOUD_ENGINES else ".wav"


def preflight(cfg: dict) -> None:
    """Fail before a long book starts, on everything that can be checked up front."""
    engine = cfg.get("engine")
    if engine in LOCAL_ENGINES and not runtime.is_installed(engine):
        raise SynthError(f"{engine} is not installed yet. Open the Engines tab and install it.")
    if engine == "chatterbox":
        if not cfg.get("voice_sample"):
            raise SynthError("Chatterbox needs a voice sample.")
        voices.sample_path(cfg["voice_sample"])  # raises if missing
    if engine == "elevenlabs":
        from . import settings

        if not settings.elevenlabs_key():
            raise SynthError("No ElevenLabs API key set. Add one in Settings.")
        if not cfg.get("voice"):
            raise SynthError("Pick an ElevenLabs voice first.")
    if engine == "edge" and not cfg.get("voice"):
        raise SynthError("Pick an Edge TTS voice first.")
    if engine not in LOCAL_ENGINES | CLOUD_ENGINES:
        raise SynthError(f"Unknown engine: {engine}")


def _worker_request(cfg: dict) -> dict:
    """The settings a local worker keeps for the whole job."""
    engine = cfg["engine"]
    if engine == "kokoro":
        request = {
            "voice": cfg.get("voice") or "af_heart",
            "speed": float(cfg.get("speed") or 1.0),
            "lang": cfg.get("lang") or "a",
        }
        if backend_for("kokoro") and backend_for("kokoro").id == "mlx":
            request["model"] = cfg.get("model_id") or KOKORO_MLX_MODEL
        return request

    lang = (cfg.get("lang") or "en").lower()
    request = {
        "ref_audio": str(voices.sample_path(cfg["voice_sample"])),
        "lang": lang,
        "temperature": float(cfg.get("temperature") or 0.55),
        "repetition_penalty": float(cfg.get("repetition_penalty") or 1.65),
    }
    backend = backend_for("chatterbox")
    if backend and backend.id == "mlx":
        request["model"] = cfg.get("model_id") or (
            CHATTERBOX_MLX_MODEL if lang == "en" else CHATTERBOX_MLX_MULTILINGUAL
        )
    return request


class SynthSession:
    """Synthesises chunks for one job, holding a worker open if the engine needs one."""

    def __init__(self, cfg: dict):
        preflight(cfg)
        self.cfg = cfg
        self.engine = cfg["engine"]
        self._worker: EngineWorker | None = None

    def __enter__(self) -> "SynthSession":
        if self.engine in LOCAL_ENGINES:
            self._worker = EngineWorker(self.engine, _worker_request(self.cfg))
            try:
                self._worker.start()
            except WorkerError as exc:
                raise SynthError(str(exc)) from exc
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None

    def synth(self, text: str, out_path: Path) -> Path:
        """Render one chunk. Returns the file actually written."""
        out_path = out_path.with_suffix(output_suffix(self.engine))
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if self.engine == "edge":
            return cloud.synth_edge(
                text,
                out_path,
                voice=self.cfg.get("voice") or "en-US-GuyNeural",
                rate=self.cfg.get("rate", "+0%"),
                pitch=self.cfg.get("pitch", "+0Hz"),
            )
        if self.engine == "elevenlabs":
            return cloud.synth_elevenlabs(
                text,
                out_path,
                voice=self.cfg.get("voice", ""),
                model_id=self.cfg.get("model_id") or "eleven_multilingual_v2",
                speed=float(self.cfg.get("speed") or 1.0),
            )

        if self._worker is None:
            raise SynthError("Synthesis session is not open")
        try:
            return self._worker.synth(text, out_path)
        except WorkerError as exc:
            raise SynthError(str(exc)) from exc


def synth_one(text: str, out_path: Path, cfg: dict) -> Path:
    """Single chunk with its own short-lived session — used for voice previews."""
    with SynthSession(cfg) as session:
        return session.synth(text, out_path)
