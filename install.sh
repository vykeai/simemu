#!/usr/bin/env bash
# simemu installer
# Usage: curl -fsSL https://raw.githubusercontent.com/vykeai/simemu/main/install.sh | bash
set -e

REPO_URL="https://github.com/vykeai/simemu.git"
INSTALL_DIR="$HOME/dev/simemu"
GUARD_SCRIPT="$HOME/.claude/simemu-guard.py"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
BLD='\033[1m'
RST='\033[0m'

ok()   { echo -e "${GRN}✓${RST} $*"; }
info() { echo -e "${BLD}→${RST} $*"; }
warn() { echo -e "${YLW}! ${RST} $*"; }
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

# ── 2. Clone / update repo ───────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing install at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only --quiet
    ok "Updated"
else
    info "Cloning simemu to $INSTALL_DIR..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned"
fi

# ── 3. pip install ───────────────────────────────────────────────────────────
info "Installing simemu command..."
"$PYTHON" -m pip install -e "$INSTALL_DIR" --quiet
ok "simemu installed ($(simemu --version 2>/dev/null || echo 'ok'))"

# ── 4. Guard hook script ─────────────────────────────────────────────────────
info "Installing guard hook script to $GUARD_SCRIPT..."
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

# ── 5. Claude settings.json hook registration ─────────────────────────────────
info "Registering guard hook in $CLAUDE_SETTINGS..."

# Create settings.json if it doesn't exist
if [ ! -f "$CLAUDE_SETTINGS" ]; then
    echo '{}' > "$CLAUDE_SETTINGS"
fi

# Use Python to safely merge the hook into existing settings
"$PYTHON" - << PYEOF
import json, sys

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

# ── 6. Optional daemon ───────────────────────────────────────────────────────
echo ""
read -r -p "Install idle-shutdown daemon? (shuts down idle simulators to reclaim RAM) [Y/n] " DAEMON_ANSWER
DAEMON_ANSWER="${DAEMON_ANSWER:-Y}"
if [[ "$DAEMON_ANSWER" =~ ^[Yy]$ ]]; then
    info "Installing daemon..."
    simemu daemon install
    ok "Daemon installed (idle timeout: 20 minutes)"
else
    info "Skipping daemon. You can install it later with: simemu daemon install"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}${BLD}simemu is ready.${RST}"
echo ""
echo "  simemu list ios"
echo "  simemu list android"
echo "  simemu acquire ios myapp-ios --device \"Soba iPhone16 6.1in iOS18\" --wait 120"
echo ""
echo "See README.md or: simemu --help"
echo ""
