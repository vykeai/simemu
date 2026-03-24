# simemu

**Session-based simulator and emulator allocation manager for multi-agent iOS and Android development.**

When multiple Claude agents build features simultaneously, they fight over simulators — one agent boots a device, another kills it, screenshots capture the wrong app. simemu solves this with session-based allocation: each agent claims a session, all operations go through `simemu do`, and direct `xcrun simctl` / `adb` calls are blocked by a guard hook.

```
Agent A                      Agent B                      Agent C
   |                            |                            |
   v                            v                            v
simemu claim ios             simemu claim ios             simemu claim android
   s-a7f3b2                     s-c4d8e1                     s-f9b2a3
   iPhone 16 Pro                iPhone 16                    Pixel 9
```

Everything runs headless by default — no windows, no dock icons, no interruptions.

---

## Requirements

- macOS (required for iOS Simulator)
- Python 3.11+
- Xcode + Command Line Tools (for iOS)
- Android Studio / SDK (for Android — optional if iOS only)

---

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/vykeai/simemu/main/install.sh | bash
```

The installer:
1. Checks Python 3.11+ is installed
2. Installs `simemu` via `pip install -e .`
3. Sets up the monitor launchd agent (lifecycle management)
4. Builds and installs the SimEmuBar menu bar app
5. Installs the guard hook into `~/.claude/settings.json`
6. Repairs broken symlinks and validates the binary path

### Manual installation

```bash
git clone https://github.com/vykeai/simemu.git ~/dev/simemu
cd ~/dev/simemu
pip3 install -e .
```

Then install the guard hook — see [Restricting agents to simemu only](#restricting-agents-to-simemu-only).

### Verify

```bash
simemu --version
simemu sessions
```

---

## Session-based API

Agents interact with sessions (opaque IDs like `s-a7f3b2`) instead of device UDIDs or AVD names. Sessions manage the full device lifecycle automatically.

### Claim a device

```bash
SESSION=$(simemu claim ios | jq -r .session)
SESSION=$(simemu claim android | jq -r .session)

# Options:
#   --version 26          specific OS version
#   --form-factor tablet  phone (default), tablet, watch, tv, vision
#   --real                prefer real device over simulator
#   --show                keep simulator window visible (default: hidden)
#   --label "my task"     label for tracking
```

### Use it

```bash
simemu do $SESSION install ./app.ipa
simemu do $SESSION launch com.example.app
simemu do $SESSION screenshot -o proof.png
simemu do $SESSION done
```

### List sessions

```bash
simemu sessions           # all active sessions
simemu sessions --json    # JSON output
```

---

## Architecture

```
Agent                         simemu                        Devices
  |                              |                              |
  |-- claim ios --------------->|-- find best device ---------->|
  |                              |-- boot if needed ----------->|
  |<-- session: s-a7f3b2 ------|                              |
  |                              |                              |
  |-- do s-a7f3b2 install ----->|-- route to ios.install() --->|
  |-- do s-a7f3b2 tap 250 500 ->|-- route to ios.tap() ------->|
  |-- do s-a7f3b2 screenshot -->|-- route to ios.screenshot()->|
  |                              |                              |
  |-- (idle 20min) ------------>|-- status: idle               |
  |-- (idle 60min) ------------>|-- status: parked, shutdown ->|
  |-- do s-a7f3b2 tap --------->|-- re-boot, back to active ->|
  |                              |                              |
  |-- do s-a7f3b2 done -------->|-- release + cleanup -------->|
```

### Session lifecycle

```
claim -> ACTIVE (device booted, agent working)
          |
          |-- idle 20min -> IDLE (device still booted, lower priority)
          |                   |
          |                   |-- agent does `do` -> back to ACTIVE
          |                   |-- idle 40min more -> PARKED (device shutdown, session preserved)
          |                                           |
          |                                           |-- agent does `do` -> re-boot, back to ACTIVE
          |                                           |-- idle 2hr -> EXPIRED (session gone)
          |
          |-- `do done` -> RELEASED (immediate)
```

### Memory budget

simemu enforces a configurable memory ceiling (default: 16GB via `SIMEMU_MEMORY_BUDGET_MB`):
- Before booting a new device, checks total estimated memory
- If over budget: parks lowest-priority idle sessions to make room
- If still over: returns error with queue info

---

## Complete command reference

All commands use the format: `simemu do $SESSION <command> [args...]`

### App lifecycle

| Command | Description |
|---|---|
| `install <path>` | Install `.app`/`.ipa` (iOS) or `.apk` (Android) |
| `launch <bundle> [args...]` | Launch app |
| `terminate <bundle>` | Force-stop app |
| `uninstall <bundle>` | Remove app |
| `reset-app <bundle> <app-path>` | Uninstall + reinstall + relaunch in one command |
| `clear-data <bundle>` | Clear app data (Android; iOS: use reset-app) |
| `is-running <bundle>` | Check if app process is running |
| `foreground-app` | Get current foreground app bundle ID |
| `app-info <bundle>` | App version, permissions, data size |
| `app-container <bundle>` | Get app data container path |
| `build [--variant X] [--clean] [--raw "cmd"]` | Build app (requires keel/execution.yaml or --raw) |

### UI interaction

| Command | Description |
|---|---|
| `a11y-tap "<label>"` | Tap by accessibility label — works headless via Maestro |
| `tap <x> <y>` | Tap at screen coordinates |
| `swipe <x1> <y1> <x2> <y2> [--duration ms]` | Swipe gesture |
| `long-press <x> <y> [--duration ms]` | Long press |
| `scroll <up\|down\|left\|right>` | Scroll in a direction |
| `back` | Go back (edge swipe on iOS, back key on Android) |
| `home` | Go to home screen |
| `type-submit <text>` | Type text + press enter |
| `input <text>` | Type text into focused field |
| `key <key-name>` | Press a named key (home, back, lock, volume_up, ...) |
| `rotate <portrait\|landscape\|left\|right>` | Rotate device orientation |
| `shake` | Shake gesture (opens React Native dev menu) |

### Permissions & alerts

| Command | Description |
|---|---|
| `grant-all <bundle>` | Grant ALL permissions (camera, location, contacts, storage, ...) |
| `dismiss-alert` | Dismiss any visible system alert |
| `accept-alert` | Tap Allow / OK on system alert |
| `deny-alert` | Tap Don't Allow / Cancel |
| `auto-dismiss` | Reset privacy warnings + disable animations (Android) |

### Capture & proof

| Command | Description |
|---|---|
| `screenshot [-o path] [-f png\|jpeg]` | Take a screenshot |
| `deeplink-proof <url> [-o path]` | Open URL + wait 3s + screenshot |
| `wait-for-render <seconds> [-o path]` | Wait N seconds + screenshot |
| `video-start [-o path]` | Start screen recording, returns PID |
| `video-stop <pid>` | Stop screen recording |
| `log-crash [bundle]` | Most recent crash report |

### Device state

| Command | Description |
|---|---|
| `appearance <light\|dark>` | Toggle light/dark mode |
| `status-bar [--time HH:MM] [--battery 0-100] [--wifi 0-3] [--clear]` | Override status bar for clean screenshots |
| `location <lat> <lng>` | Set GPS location override |
| `network <offline\|slow\|normal>` | Set network mode (Android only) |
| `keychain-reset` | Clear keychain (iOS only) |
| `icloud-sync` | Trigger iCloud sync (iOS only) |
| `font-size <small\|default\|large\|xlarge>` | Set font scale (Android only) |
| `reduce-motion <on\|off>` | Disable/enable animations (Android only) |
| `notifications-clear` | Clear all notifications (Android only) |

### Clipboard

| Command | Description |
|---|---|
| `clipboard-set <text>` | Set clipboard contents |
| `clipboard-get` | Read clipboard (iOS only) |

### Files & media

| Command | Description |
|---|---|
| `url <url>` | Open URL / deep link |
| `add-media <file>` | Add photo/video to Photos/Gallery |
| `push <local> <remote>` | Push file to device (Android only) |
| `pull <remote> <local>` | Pull file from device (Android only) |
| `contacts-import <vcf-file>` | Import contacts from VCF file |

### Maestro integration

| Command | Description |
|---|---|
| `maestro <flow.yaml> [extra...]` | Run Maestro flow (resolves device ID automatically) |

### Accessibility

| Command | Description |
|---|---|
| `a11y-tap "<label>"` | Tap by accessibility label — headless via Maestro |
| `a11y-tree` | Dump accessibility tree (Android only) |

### Session management

| Command | Description |
|---|---|
| `done` | Release session |
| `boot` | Wake a parked session |
| `show` | Make simulator window visible |
| `hide` | Hide simulator window |
| `renew [--hours N]` | Extend session heartbeat |
| `reboot` | Restart the simulator |
| `env` | Device info (UDID/serial, OS version, form factor) |
| `clone [name]` | Clone iOS simulator |
| `siri <query>` | Siri query (limited support) |

---

## Window management

Simulators run headless by default. Use `show` / `hide` to control visibility:

```bash
simemu do $SESSION show     # make visible
simemu do $SESSION hide     # hide again
```

Or claim with `--show` to keep the window visible from the start:

```bash
SESSION=$(simemu claim ios --show | jq -r .session)
```

---

## Monitor and menubar app

simemu includes a macOS menubar app for monitoring active sessions:

```bash
simemu monitor              # launch the menubar app
```

The menubar app shows:
- Active sessions with device names and labels
- Session status (active, idle, parked)
- Quick actions to show/hide/release sessions

---

## HTTP API

simemu auto-starts a background API server on `127.0.0.1:8765`:

```
POST /v2/claim    -> ClaimSpec body -> Session JSON
POST /v2/do       -> {session, command, args} -> result
GET  /v2/sessions -> list all active sessions
```

Disable autostart when needed:

```bash
SIMEMU_NO_AUTOSTART=1 simemu sessions
```

---

## State files

- `~/.simemu/sessions.json` — session state (locked via fcntl)

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SIMEMU_AGENT` | `pid-<PID>` | Agent identity shown in `simemu sessions` |
| `SIMEMU_OUTPUT_DIR` | `~/.simemu/` | Where screenshots and recordings are saved |
| `SIMEMU_SCREENSHOT_MAX_SIZE` | — | Auto-resize screenshots to this pixel limit |
| `SIMEMU_MEMORY_BUDGET_MB` | `16384` | Memory ceiling for all running devices (MB) |
| `SIMEMU_NO_AUTOSTART` | — | Set to `1` to disable auto-starting the API server |
| `SIMEMU_HUD` | `1` | Set to `0` to disable the HUD overlay during visible operations |

---

## Restricting agents to simemu only

### Guard script — `~/.claude/simemu-guard.py`

```python
import json, sys, re

payload = json.load(sys.stdin)
cmd = payload.get("tool_input", {}).get("command", "")

BLOCKED = [
    r"xcrun\s+simctl",
    r"xcrun\s+xctrace",
    r"\badb\b.*(install|shell|logcat|pull|push|uninstall)",
    r"emulator\s+-avd",
    r"avdmanager\s+create\s+avd",
]

def scrub(text):
    text = re.sub(r"<<'[A-Z]+?'.*?[A-Z]+", "", text, flags=re.DOTALL)
    text = re.sub(r"\$\(cat\s+<<.*?\)", "", text, flags=re.DOTALL)
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', text)
    return text

if any(re.search(p, scrub(cmd)) for p in BLOCKED):
    print(json.dumps({
        "decision": "block",
        "reason": "Use simemu instead. xcrun simctl / adb calls are blocked. See: simemu --help"
    }))
else:
    print(json.dumps({"decision": "approve"}))
```

### Register it — `~/.claude/settings.json`

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/simemu-guard.py"}]
      }
    ]
  }
}
```

`install.sh` does both steps automatically.

---

## Adding simemu to a project

Copy `docs/AGENT_README.md` into your project's `AGENTS.md` file. That file contains the complete v2 command reference agents need.

---

## License

MIT
