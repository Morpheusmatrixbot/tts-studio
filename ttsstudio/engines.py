"""Engine catalogue and per-platform backend selection.

Two engines run locally (Kokoro, Chatterbox) and each has two interchangeable
backends: an MLX one that uses the Apple Neural Engine / Metal on Apple Silicon,
and a portable one (ONNX Runtime or PyTorch) for Intel Macs, Windows and Linux.
The rest of the app only ever names the *engine*; this module decides which
backend that means on the machine it is running on.

Two more engines need no install at all: Edge TTS speaks over the network for
free, and ElevenLabs needs only an API key.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}


@dataclass(frozen=True)
class Backend:
    """One concrete way to run an engine on one class of machine."""

    id: str
    label: str
    packages: list[str]
    worker: str
    # Direct file downloads (url, filename, approx_bytes) needed on top of pip.
    files: list[tuple[str, str, int]] = field(default_factory=list)
    # Rough disk cost so the UI can warn before a multi-GB install.
    approx_mb: int = 0
    accelerator: str = "CPU"


KOKORO_ONNX_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

# Kokoro's English G2P (misaki) calls spacy.load("en_core_web_sm"), and spaCy
# models are not on PyPI — pip installs spaCy happily and then fails at runtime
# with "Can't find model 'en_core_web_sm'". Pinning the wheel here makes the
# install self-sufficient instead of leaving a trap for the first narration.
SPACY_EN_SM = (
    "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)

BACKENDS: dict[str, dict[str, Backend]] = {
    "kokoro": {
        "mlx": Backend(
            id="mlx",
            label="Apple Silicon (MLX)",
            packages=[
                "mlx-audio>=0.5.0",
                "misaki[en]>=0.9.4",
                SPACY_EN_SM,
                "soundfile>=0.12.1",
                "numpy>=1.26",
            ],
            worker="kokoro_mlx.py",
            approx_mb=900,
            accelerator="Apple GPU (Metal)",
        ),
        "onnx": Backend(
            id="onnx",
            label="Portable (ONNX Runtime)",
            packages=["kokoro-onnx>=0.6.1", "soundfile>=0.12.1", "numpy>=2.0"],
            worker="kokoro_onnx.py",
            files=[
                (f"{KOKORO_ONNX_BASE}/kokoro-v1.0.onnx", "kokoro-v1.0.onnx", 325_532_387),
                (f"{KOKORO_ONNX_BASE}/voices-v1.0.bin", "voices-v1.0.bin", 28_214_398),
            ],
            approx_mb=700,
            accelerator="CPU",
        ),
    },
    "chatterbox": {
        "mlx": Backend(
            id="mlx",
            label="Apple Silicon (MLX)",
            packages=["mlx-audio>=0.5.0", "soundfile>=0.12.1", "numpy>=1.26"],
            worker="chatterbox_mlx.py",
            approx_mb=3500,
            accelerator="Apple GPU (Metal)",
        ),
        "torch": Backend(
            id="torch",
            label="Portable (PyTorch)",
            packages=["chatterbox-tts>=0.1.7", "soundfile>=0.12.1"],
            worker="chatterbox_torch.py",
            approx_mb=6000,
            accelerator="CUDA GPU if present, else CPU",
        ),
    },
}


@dataclass(frozen=True)
class Engine:
    id: str
    label: str
    kind: str  # "local" | "cloud"
    summary: str
    needs_install: bool = False
    needs_api_key: bool = False
    supports_voice_clone: bool = False
    supports_voice_presets: bool = False


ENGINES: list[Engine] = [
    Engine(
        id="kokoro",
        label="Kokoro",
        kind="local",
        summary="Fast offline narration with 28 built-in voices. Best default for long books.",
        needs_install=True,
        supports_voice_presets=True,
    ),
    Engine(
        id="chatterbox",
        label="Chatterbox",
        kind="local",
        summary="Offline voice cloning — give it a short sample and it narrates in that voice.",
        needs_install=True,
        supports_voice_clone=True,
    ),
    Engine(
        id="edge",
        label="Edge TTS",
        kind="cloud",
        summary="Free Microsoft cloud voices, 100+ languages. No install, needs internet.",
    ),
    Engine(
        id="elevenlabs",
        label="ElevenLabs",
        kind="cloud",
        summary="Premium cloud voices. Needs your own API key.",
        needs_api_key=True,
    ),
]

ENGINE_BY_ID = {e.id: e for e in ENGINES}


def backend_for(engine_id: str) -> Backend | None:
    """The backend this machine should use for an engine, or None if cloud-only."""
    table = BACKENDS.get(engine_id)
    if not table:
        return None
    if is_apple_silicon() and "mlx" in table:
        return table["mlx"]
    for fallback in ("onnx", "torch"):
        if fallback in table:
            return table[fallback]
    return next(iter(table.values()), None)


def platform_label() -> str:
    if is_apple_silicon():
        return "macOS (Apple Silicon)"
    if sys.platform == "darwin":
        return "macOS (Intel)"
    if sys.platform == "win32":
        return "Windows"
    return "Linux"
