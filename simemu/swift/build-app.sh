#!/usr/bin/env bash
# build-app.sh — builds SimEmuBar.app bundle
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/SimEmuBar.app"
CONTENTS="$APP_DIR/Contents"

echo "→ Building SimEmuBar (release)..."
cd "$SCRIPT_DIR"
swift build -c release 2>&1

BINARY="$SCRIPT_DIR/.build/release/SimEmuBar"
if [ ! -f "$BINARY" ]; then
    echo "✗ Build failed: binary not found at $BINARY"
    exit 1
fi

echo "→ Assembling SimEmuBar.app..."
rm -rf "$APP_DIR"
mkdir -p "$CONTENTS/MacOS"
mkdir -p "$CONTENTS/Resources"

cp "$BINARY" "$CONTENTS/MacOS/SimEmuBar"
cp "$SCRIPT_DIR/AppInfo.plist" "$CONTENTS/Info.plist"

echo "→ Done: $APP_DIR"
echo ""
echo "To run now: open '$APP_DIR'"
