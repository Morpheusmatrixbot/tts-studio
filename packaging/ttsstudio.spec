# PyInstaller spec — builds the desktop app for macOS and Windows.
#
# Deliberately small: only the app shell is frozen (server, UI, document
# parsing, the two cloud engines). The local neural engines are several
# gigabytes and are installed on demand at runtime by ttsstudio/runtime.py,
# so they must NOT be collected here.
#
#   pyinstaller packaging/ttsstudio.spec --noconfirm

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
APP_NAME = "TTS Studio"
VERSION = "1.1.0"

datas = [
    (str(ROOT / "ttsstudio" / "web"), "ttsstudio/web"),
    # Worker scripts are executed by each engine's own interpreter, so they ship
    # as plain source rather than being imported into this bundle.
    (str(ROOT / "ttsstudio" / "workers"), "ttsstudio/workers"),
]
# soundfile carries the libsndfile binary that also gives us MP3 export.
datas += collect_data_files("soundfile")

hiddenimports = [
    "ttsstudio", "ttsstudio.server", "ttsstudio.jobs", "ttsstudio.runtime",
    "ttsstudio.engines", "ttsstudio.synth", "ttsstudio.audio", "ttsstudio.extract",
    "ttsstudio.voices", "ttsstudio.cloud", "ttsstudio.settings", "ttsstudio.paths",
    "edge_tts", "pypdf", "soundfile", "numpy",
]

excludes = [
    # Never let a dev environment's heavyweight ML stack leak into the installer.
    "torch", "torchaudio", "mlx", "mlx_audio", "transformers", "spacy",
    "onnxruntime", "kokoro_onnx", "chatterbox", "matplotlib", "tkinter",
    "scipy", "pandas", "IPython", "pytest",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon_mac = ROOT / "packaging" / "icon.icns"
icon_win = ROOT / "packaging" / "icon.ico"

if sys.platform == "darwin":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=APP_NAME,
        console=False,
        argv_emulation=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(icon_mac) if icon_mac.exists() else None,
        bundle_identifier="io.github.ttsstudio",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # No dock-less mode: the user needs a way to quit the server.
            "LSUIElement": False,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "MIT licensed",
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="TTSStudio",
        console=False,
        icon=str(icon_win) if icon_win.exists() else None,
        version=str(ROOT / "packaging" / "win_version.txt")
        if (ROOT / "packaging" / "win_version.txt").exists()
        else None,
    )
