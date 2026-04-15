#!/usr/bin/env bash
# simemu installer — idempotent, safe to re-run
# Usage: bash /Users/luke/dev/simemu/install.sh
#    or: curl -fsSL https://raw.githubusercontent.com/vykeai/simemu/main/install.sh | bash
set -e

REPO_URL="https://github.com/vykeai/simemu.git"
DEFAULT_CLONE_DIR="$HOME/dev/simemu"

# When piped via curl, BASH_SOURCE[0] is empty — detect and clone the repo first
if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]}" == "bash" || "${BASH_SOURCE[0]}" == "/dev/stdin" ]]; then
    SCRIPT_DIR=""
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -z "$SCRIPT_DIR" ]]; then
    # Running via curl pipe — clone the repo then re-exec from the clone
    INSTALL_DIR="${SIMEMU_INSTALL_DIR:-$DEFAULT_CLONE_DIR}"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        echo "→ Updating existing clone at $INSTALL_DIR"
        git -C "$INSTALL_DIR" pull --quiet
    else
        echo "→ Cloning simemu to $INSTALL_DIR"
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    fi
    exec bash "$INSTALL_DIR/install.sh"
fi

INSTALL_DIR="${SIMEMU_INSTALL_DIR:-$SCRIPT_DIR}"
GUARD_SCRIPT="$HOME/.claude/simemu-guard.py"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
PLIST_LABEL="com.simemu.monitor"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
SWIFT_DIR="$INSTALL_DIR/simemu/swift"
APP_INSTALL_DIR="/Applications"
SIMEMU_DATA_DIR="$HOME/.simemu"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
BLD='\033[1m'
RST='\033[0m'

ok()   { echo -e "${GRN}✓${RST} $*"; }
info() { echo -e "${BLD}→${RST} $*"; }
warn() { echo -e "${YLW}!${RST} $*"; }
die()  { echo -e "${RED}✗${RST} $*" >&2; exit 1; }

echo ""
echo -e "${BLD}simemu — simulator allocation manager${RST}"
echo "────────────────────────────────────────"
echo ""

# ── 1. Python 3.11+ ──────────────────────────────────────────────────────────
info "Checking Python version..."
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    die "python3 not found. Install Python 3.11+ from https://python.org or via Homebrew: brew install python"
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    die "Python 3.11+ required, found $PY_VERSION. Install a newer version: brew install python"
fi
ok "Python $PY_VERSION"

# ── 2. Install simemu via pip ────────────────────────────────────────────────
info "Installing simemu package..."
"$PYTHON" -m pip install -e "$INSTALL_DIR" --quiet 2>/dev/null || \
    "$PYTHON" -m pip install -e "$INSTALL_DIR" --quiet --break-system-packages
ok "simemu package installed"

# ── 3. Monitor launchd agent ────────────────────────────────────────────────
info "Installing monitor launchd agent..."
mkdir -p "$PLIST_DIR"
mkdir -p "$SIMEMU_DATA_DIR"

# Resolve the python3 binary path for the plist
PYTHON_ABS=$("$PYTHON" -c "import sys; print(sys.executable)")
# Build PATH with essential directories
PLIST_PATH_VAR="$(dirname "$PYTHON_ABS"):/usr/local/lib/android/sdk/platform-tools:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"

# Unload existing agent if loaded (ignore errors)
launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || \
    launchctl unload "$PLIST_PATH" 2>/dev/null || true

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_ABS</string>
        <string>-m</string>
        <string>simemu.monitor</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SIMEMU_DATA_DIR/monitor-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$SIMEMU_DATA_DIR/monitor-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$INSTALL_DIR</string>
        <key>PATH</key>
        <string>$PLIST_PATH_VAR</string>
    </dict>
</dict>
</plist>
PLIST

launchctl load "$PLIST_PATH" 2>/dev/null || \
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
ok "Monitor agent installed at $PLIST_PATH"

# ── 4. Build and install menubar app ─────────────────────────────────────────
if [ -d "$SWIFT_DIR" ] && command -v swift >/dev/null 2>&1; then
    info "Building menubar app (SimEmuBar)..."
    if (cd "$SWIFT_DIR" && make app 2>/dev/null); then
        BUILT_APP="$SWIFT_DIR/.build/SimEmuBar.app"
        if [ -d "$BUILT_APP" ]; then
            # Kill running instance if any
            pkill -f "SimEmuBar" 2>/dev/null || true
            sleep 0.5
            rm -rf "$APP_INSTALL_DIR/SimEmuBar.app"
            cp -R "$BUILT_APP" "$APP_INSTALL_DIR/SimEmuBar.app"
            ok "SimEmuBar installed to $APP_INSTALL_DIR/SimEmuBar.app"
            # Launch it
            open "$APP_INSTALL_DIR/SimEmuBar.app" 2>/dev/null || true
        else
            warn "Build succeeded but .app bundle not found at $BUILT_APP"
        fi
    else
        warn "Menubar app build failed (non-fatal — swift toolchain may be missing)"
    fi
else
    if [ ! -d "$SWIFT_DIR" ]; then
        warn "Menubar app source not found at $SWIFT_DIR — skipping"
    else
        warn "swift not found — skipping menubar app build"
    fi
fi

# ── 5. Guard hook script ────────────────────────────────────────────────────
info "Installing guard hook script..."
mkdir -p "$(dirname "$GUARD_SCRIPT")"
cat > "$GUARD_SCRIPT" << 'GUARD'
import json, sys, re

payload = json.load(sys.stdin)
cmd = payload.get("tool_input", {}).get("command", "")

BLOCKED = [
    r"xcrun\s+simctl",
    r"xcrun\s+xctrace",
    r"\badb\b.*(install|shell|logcat|pull|push|uninstall)",
    r"emulator\s+-avd",
    r"avdmanager\s+create\s+avd",
    r"\bmaestro\b.*(--device|test\b)",  # use: simemu maestro <slug> <flow.yaml>
]

def scrub(text):
    text = re.sub(r"<<'[A-Z]+?'.*?[A-Z]+", "", text, flags=re.DOTALL)
    text = re.sub(r"\$\(cat\s+<<.*?\)", "", text, flags=re.DOTALL)
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r'"[^"]*"', '""', text)
    return text

if any(re.search(p, scrub(cmd)) for p in BLOCKED):
    print(json.dumps({
        "decision": "block",
        "reason": "Use simemu instead. xcrun simctl / adb calls are blocked. See: simemu --help"
    }))
else:
    print(json.dumps({"decision": "approve"}))
GUARD
chmod +x "$GUARD_SCRIPT"
ok "Guard script written"

# ── 6. Claude settings.json hook registration ───────────────────────────────
info "Registering guard hook in $CLAUDE_SETTINGS..."

if [ ! -f "$CLAUDE_SETTINGS" ]; then
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    echo '{}' > "$CLAUDE_SETTINGS"
fi

"$PYTHON" - << PYEOF
import json

path = "$CLAUDE_SETTINGS"
guard = "$GUARD_SCRIPT"

with open(path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
pretool = hooks.setdefault("PreToolUse", [])

hook_entry = {
    "type": "command",
    "command": f"python3 {guard}"
}
bash_hook = None
for entry in pretool:
    if entry.get("matcher") == "Bash":
        bash_hook = entry
        break

if bash_hook is None:
    pretool.append({"matcher": "Bash", "hooks": [hook_entry]})
else:
    existing = bash_hook.setdefault("hooks", [])
    if not any(h.get("command", "").endswith("simemu-guard.py") for h in existing):
        existing.append(hook_entry)

with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PYEOF
ok "Guard hook registered"

# ── 7. Verify installation ──────────────────────────────────────────────────
echo ""
info "Verifying installation..."
ERRORS=0

# Check simemu --version
if SIMEMU_VER=$(simemu --version 2>&1); then
    ok "simemu --version: $SIMEMU_VER"
else
    warn "simemu --version failed"
    ERRORS=$((ERRORS + 1))
fi

# Check simemu sessions
if simemu sessions --json >/dev/null 2>&1; then
    ok "simemu sessions: working"
else
    warn "simemu sessions failed (server may not be running yet)"
fi

# Check monitor agent
if launchctl list "$PLIST_LABEL" >/dev/null 2>&1; then
    ok "Monitor agent: loaded"
else
    warn "Monitor agent: not loaded (may need login/logout)"
fi

# Check menubar app
if pgrep -fl "SimEmuBar" >/dev/null 2>&1; then
    ok "SimEmuBar: running"
elif [ -d "$APP_INSTALL_DIR/SimEmuBar.app" ]; then
    ok "SimEmuBar: installed (not running)"
else
    info "SimEmuBar: not installed"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GRN}${BLD}simemu is ready.${RST}"
else
    echo -e "${YLW}${BLD}simemu installed with warnings.${RST}"
fi
echo ""
echo "  simemu claim ios"
echo "  simemu claim android"
echo "  simemu claim macos"
echo "  simemu sessions"
echo "  simemu status"
echo ""
echo "See README.md or: simemu --help"
echo ""
