<div align="center">

<img src="packaging/icon.png" width="120" alt="TTS Studio">

# TTS Studio

**Turn text and books into narrated audio — on your own computer.**

Paste text or drop in a `.txt`, `.epub` or `.pdf`, pick a voice, get an audiobook.
Local engines run fully offline; nothing you narrate leaves your machine.

[Download](#download) · [Engines](#engines) · [Run from source](#run-from-source) · [Русский](README.ru.md)

</div>

---

## What it does

- **Any text, any length.** Paste a paragraph or drop in a whole novel. EPUBs are split into
  chapters automatically; each chapter is exported as its own file alongside the full book.
- **Four engines, one interface.** Two run offline on your own hardware, two are cloud services.
- **Voice cloning.** Give Chatterbox a 5–20 second sample and it narrates in that voice.
- **Many languages.** Kokoro covers 9, Chatterbox 23, Edge TTS over 100.
- **Real progress.** A long book shows percentage, pieces completed, elapsed time and a
  running estimate of how much longer it will take.
- **Interface in English and Russian.**
- **WAV and MP3 output**, saved wherever you point it.

## Download

Grab the installer for your system from the [**Releases page**](../../releases/latest).

| System | File |
|---|---|
| macOS (Apple Silicon — M1/M2/M3/M4) | `TTS-Studio-<version>-macOS-arm64.dmg` |
| macOS (Intel) | `TTS-Studio-<version>-macOS-x86_64.dmg` |
| Windows 10/11 (64-bit) | `TTS-Studio-<version>-Windows-Setup.exe` |
| Windows (no installer) | `TTS-Studio-<version>-Windows-portable.zip` |

The installer is small (about 20 MB). It contains the application and the two cloud
engines, which work immediately. The offline engines are large, so they are downloaded
later — only if you ask for them — from the **Engines** tab inside the app.

### First launch

The app opens in your web browser at `http://127.0.0.1:8766`. That is the interface;
the app itself keeps running until you quit it.

> **The builds are not code-signed**, because a signing certificate is a paid,
> identity-bound subscription from Apple and Microsoft. Both systems will warn you
> the first time. This is what an unsigned app looks like — not a virus warning.

**macOS** — right-click (or Control-click) the app in Applications, choose **Open**, then
confirm. Once only. If macOS still refuses:

```bash
xattr -dr com.apple.quarantine "/Applications/TTS Studio.app"
```

**Windows** — SmartScreen shows "Windows protected your PC". Click **More info**, then
**Run anyway**.

If you would rather not trust a prebuilt binary at all, [run it from source](#run-from-source) —
it is the same code and takes two commands.

## Engines

| Engine | Runs | Voices | Languages | Install | Needs |
|---|---|---|---|---|---|
| **Kokoro** | offline | 51 presets | 9 | ~900 MB | — |
| **Chatterbox** | offline | clones your sample | 23 | 3.5–6 GB | a voice sample |
| **Edge TTS** | cloud | 300+ | 100+ | none | internet |
| **ElevenLabs** | cloud | your account's | 30+ | none | API key |

**Kokoro** is the right default for a long book: fast, stable, and it sounds like a
narrator. **Chatterbox** is what you want when the voice matters more than the speed —
it is considerably slower and needs a clean sample to clone from.

On Apple Silicon both offline engines run on the GPU through MLX. On Windows, Linux and
Intel Macs they use ONNX Runtime and PyTorch instead; the app picks the right one for
your machine and tells you which it chose.

### Installing an offline engine

Open the **Engines** tab and press **Install**. The app downloads a private Python
runtime and the model weights into its own data folder, showing progress as it goes.
Nothing is installed system-wide, and **Remove** deletes it again.

Everything lives in one folder, which you can delete to uninstall completely:

| System | Folder |
|---|---|
| macOS | `~/Library/Application Support/TTS Studio` |
| Windows | `%LOCALAPPDATA%\TTS Studio` |
| Linux | `~/.local/share/tts-studio` |

### ElevenLabs

ElevenLabs is a paid cloud service and needs your own key:

1. Sign in at [elevenlabs.io](https://elevenlabs.io) and open
   [**Settings → API Keys**](https://elevenlabs.io/app/settings/api-keys).
2. Create a key and copy it.
3. In TTS Studio open **Settings**, paste it, press **Check and save**. The app verifies
   the key against your account and shows your plan and remaining characters.
4. Back on the **Narrate** tab, choose ElevenLabs and press **Load my voices**.

The key is stored in your own data folder on your own computer. It is never sent
anywhere except to ElevenLabs, and the app never returns it to the browser once saved.

## Run from source

Works on macOS, Windows and Linux. Needs Python 3.10 or newer.

```bash
git clone https://github.com/Morpheusmatrixbot/tts-studio.git
cd tts-studio
pip install -r requirements.txt
python -m ttsstudio
```

Your browser opens at `http://127.0.0.1:8766`. Offline engines install from the
**Engines** tab exactly as they do in the packaged app — the source and the installer
run the same code.

Useful flags:

```bash
python -m ttsstudio --port 9000     # use a different port
python -m ttsstudio --no-browser    # do not open a browser tab
```

## Tips for long books

- **Words per chunk** (under *Advanced*) is the main quality dial. Neural models drift on
  long inputs, so the text is narrated in pieces and stitched back together. Smaller
  pieces are more reliable; larger ones flow better. 55 is a good balance, 30–40 is
  safer for Chatterbox.
- **Estimate the time first.** Uploading a file shows the word count and roughly how many
  minutes of audio it will produce. Narration is usually faster than real time with
  Kokoro and slower than real time with Chatterbox.
- **Cancel is safe.** Stopping a job keeps every chapter finished so far.

## Building the installers yourself

```bash
pip install -r requirements.txt pyinstaller
python packaging/make_icon.py
pyinstaller packaging/ttsstudio.spec --noconfirm

bash packaging/make_dmg.sh 1.0.0                       # macOS → dist/*.dmg
iscc /DAppVersion=1.0.0 packaging\windows\installer.iss # Windows → dist/*Setup.exe
```

PyInstaller cannot cross-compile, so each installer must be built on its own operating
system. [`.github/workflows/build.yml`](.github/workflows/build.yml) does all of them on
GitHub's runners and attaches the results to a release when a `v*` tag is pushed.

## Tests

```bash
pip install pytest
python -m pytest tests -v
```

## Privacy

Kokoro and Chatterbox run entirely on your machine — your text is never transmitted.
Edge TTS and ElevenLabs are cloud services, so text sent to them leaves your computer by
definition; use an offline engine if that matters. The app binds to `127.0.0.1` only and
rejects requests that do not come from your own machine.

## Credits

Built on [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0),
[Chatterbox](https://github.com/resemble-ai/chatterbox) by Resemble AI (MIT),
[mlx-audio](https://github.com/Blaizzy/mlx-audio),
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx),
[edge-tts](https://github.com/rany2/edge-tts) and [uv](https://github.com/astral-sh/uv).

## License

MIT — see [LICENSE](LICENSE). The model weights each carry their own licence from their
respective authors.
