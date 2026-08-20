#!/bin/bash
# Build the macOS disk image from an already-built dist/TTS Studio.app.
#
#   python packaging/make_icon.py
#   pyinstaller packaging/ttsstudio.spec --noconfirm
#   bash packaging/make_dmg.sh [version]
#
# Produces dist/TTS-Studio-<version>-macOS-<arch>.dmg containing the app and a
# shortcut to /Applications, so installing is a drag from one to the other.

set -euo pipefail

VERSION="${1:-1.0.0}"
APP_NAME="TTS Studio"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PATH="$ROOT/dist/$APP_NAME.app"
ARCH="$(uname -m)"
DMG_PATH="$ROOT/dist/TTS-Studio-$VERSION-macOS-$ARCH.dmg"
STAGE="$ROOT/build/dmg"

if [ ! -d "$APP_PATH" ]; then
  echo "error: $APP_PATH not found — run pyinstaller first" >&2
  exit 1
fi

rm -rf "$STAGE" "$DMG_PATH"
mkdir -p "$STAGE"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# A short read-me rides along in the image; most people open it before the app.
cat > "$STAGE/READ ME FIRST.txt" <<'EOF'
TTS Studio
==========

INSTALL
  Drag "TTS Studio" onto the Applications folder shown here.

FIRST LAUNCH
  macOS blocks apps from unidentified developers on the first open.
  Right-click (or Control-click) the app in Applications and choose "Open",
  then confirm. You only have to do this once.

  If macOS still refuses, open Terminal and run:
      xattr -dr com.apple.quarantine "/Applications/TTS Studio.app"

USING IT
  The app opens in your web browser at http://127.0.0.1:8766
  Edge TTS and ElevenLabs work immediately.
  Kokoro and Chatterbox run offline and are downloaded from the
  "Engines" tab the first time you use them.

  Quit the app from the Dock when you are finished.

Source and documentation: https://github.com/Morpheusmatrixbot/tts-studio
EOF

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$DMG_PATH"

rm -rf "$STAGE"
echo "Built: $DMG_PATH"
ls -lh "$DMG_PATH"
