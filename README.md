# 🦤 simemu

**Simulator allocation manager for multi-agent iOS and Android development.**

When multiple Claude agents build different features simultaneously, they fight over the same simulators — one agent boots a device, another kills it, screenshots capture the wrong app. `simemu` solves this with named reservations: each agent owns a slug, all operations go through the CLI, and direct `xcrun simctl` / `adb` calls are blocked by a guard hook.

```
Agent A                      Agent B                      Agent C
   │                            │                            │
   ▼                            ▼                            ▼
simemu acquire ios           simemu acquire ios           simemu acquire android
   fitkind-ios                  healthapp-ios                fitkind-android
   ──────────                   ─────────────                ───────────────
   iPhone 16 Pro ✓              iPhone 16 ✓                  Pixel 9 ✓
```

> 📸 **Screenshots are the proof of delivery — never verbal claims.**
> Save them to `~/Desktop/screenshots/{project-name}/`, not `/tmp`.

---

## ✨ Features

- 🏷️ **Named reservations** — agents work with slugs (`fitkind-ios`), not raw UDIDs or serials
- 🔀 **Platform dispatch** — same commands on iOS and Android; the slug carries the platform
- 🖥️ **Headless Android by default** — boots without a window, saving 300–700 MB RAM per emulator
- 👆 **Full gesture proxy** — tap, swipe, long-press with `--pct` (percentage coordinates), rotate, key events
- 🧭 **Shared-desktop readiness** — `present` and `stabilize` make iOS simulator interaction safer on a human-used Mac
- 📷 **Capture pipeline** — screenshot, screen recording, crash logs, side-by-side comparison
- ♻️ **App lifecycle** — install (with timeout), launch, terminate, `reset-app` (force-stop + clear + relaunch in one command)
- 📡 **Device state** — GPS, permissions, network, battery, status bar, biometrics, appearance
- 🤖 **Genymotion support** — works alongside standard AVDs with zero configuration
- 🎭 **Maestro integration** — `simemu maestro <slug> <flow.yaml>` resolves device IDs automatically
- 💤 **Idle shutdown daemon** — shuts down idle simulators to reclaim memory, runs as a launchd agent
- 🛡️ **Guard hook** — Claude Code hook blocks all direct `xcrun simctl` / `adb` / `emulator` calls
- 📄 **JSON output** — every read command supports `--json` for scripting

---

## 📋 Requirements

- macOS (required for iOS Simulator)
- Python 3.11+
- Xcode + Command Line Tools (for iOS)
- Android Studio / SDK (for Android — optional if iOS only)
- Genymotion Desktop (optional)
- ImageMagick (optional — `brew install imagemagick` for `simemu compare`)

---

## 🚀 Installation

```bash
curl -fsSL https://raw.githubusercontent.com/vykeai/simemu/main/install.sh | bash
```

The installer:
1. Checks Python 3.11+ is installed
2. Clones the repo to `~/dev/simemu`
3. Installs the `simemu` command via `pip install -e .`
4. Installs the guard hook into `~/.claude/settings.json`
5. Optionally installs the idle-shutdown daemon

### Manual installation

```bash
git clone https://github.com/vykeai/simemu.git ~/dev/simemu
cd ~/dev/simemu
pip3 install -e .
```

Then install the guard hook — see [Restricting agents to simemu only](#-restricting-agents-to-simemu-only).

### Verify

```bash
simemu --version    # simemu 0.1.0
simemu list ios
simemu list android
```

---

## ⚡ Quick start

### 1. Check what's available

```bash
simemu list ios        # available iOS simulators (unreserved)
simemu list android    # available Android emulators / Genymotion VMs
simemu status          # see all current reservations
```

### 2. Acquire a simulator

```bash
# iOS — auto-picks a free simulator
simemu acquire ios myapp-ios

# iOS — request a specific device, wait up to 2 minutes if busy
simemu acquire ios myapp-ios --device "Soba iPhone16 6.1in iOS18" --wait 120

# Android — headless by default (no window, saves 300–700 MB RAM)
simemu acquire android myapp-android --device "Biscuit MedPhone 6.3in API35" --wait 120

# Android — with a visible window (for direct observation only)
simemu acquire android myapp-android --device "Biscuit MedPhone 6.3in API35" --window
```

### 3. Install and test your app

```bash
# Install with timeout (raises clear error + reboot hint if the emulator hangs)
simemu install myapp-ios  path/to/App.app --timeout 60
simemu launch  myapp-ios  com.example.myapp

# Prove it works with a screenshot
export PROJECT_SCREENSHOT_DIR=~/Desktop/screenshots/$(basename "$(git rev-parse --show-toplevel)")
mkdir -p "$PROJECT_SCREENSHOT_DIR"
simemu screenshot myapp-ios --max-size 1000 -o "$PROJECT_SCREENSHOT_DIR/ios_home.png"

# Reset app to clean state in one command
simemu reset-app myapp-ios com.example.myapp
```

### 4. Set your agent identity

```bash
export SIMEMU_AGENT=myapp   # short, consistent, memorable
```

Without this, simemu uses `pid-<PID>` — which means nobody can release the slug after the process exits. Set it in your shell profile or Claude project settings.

### 5. Release when done

```bash
simemu release myapp-ios
simemu release myapp-android
```

> Reservations are **permanent** — they never expire automatically. Only an explicit `release` removes them. This is intentional: auto-reclaim caused agents to steal each other's simulators.

---

## 📖 All commands

### 🏷️ Reservation

| Command | Description |
|---|---|
| `simemu acquire <ios\|android> <slug> [--device <name>] [--wait <sec>] [--window] [--no-boot] [--json]` | Reserve and boot a simulator |
| `simemu release <slug>` | Release reservation |
| `simemu rename <slug> <new-name>` | Rename a reservation |
| `simemu delete <slug> [--yes]` | Delete the physical simulator and release |
| `simemu status [--json]` | Show all active reservations |
| `simemu list [ios\|android] [--json]` | Show available (unreserved) simulators |

### 🖥️ Simulator control

| Command | Description |
|---|---|
| `simemu boot <slug> [--window]` | Boot a reserved simulator |
| `simemu shutdown <slug>` | Shut down without releasing |
| `simemu reboot <slug>` | Shut down and boot again (fastest fix for hung emulators) |
| `simemu present <slug> [--json]` | Restore an iOS simulator window into a known visible state |
| `simemu stabilize <slug> [--json]` | Preflight simulator readiness for interactive work |
| `simemu erase <slug> [--yes]` | Factory reset — wipes all data |
| `simemu check <slug> [--bundle <id>]` | Verify simulator is booted; optionally check app is in foreground |
| `simemu env <slug>` | Show device info (UDID, screen size, Maestro device ID) as JSON |

### 👆 UI interaction

| Command | Description |
|---|---|
| `simemu tap <slug> <x> <y> [--pct]` | Tap at screen coordinates |
| `simemu swipe <slug> <x1> <y1> <x2> <y2> [--duration <ms>] [--pct]` | Swipe gesture |
| `simemu long-press <slug> <x> <y> [--duration <ms>] [--pct]` | Long press |
| `simemu key <slug> <key>` | Press a key (`home`, `back`, `lock`, `siri`, `volume_up`, …) |
| `simemu rotate <slug> portrait\|landscape\|left\|right` | Rotate device orientation |
| `simemu appearance <slug> light\|dark` | Toggle light/dark mode |
| `simemu shake <slug>` | Shake gesture (opens React Native dev menu) |
| `simemu input <slug> <text>` | Type text into focused field |
| `simemu status-bar <slug> [--time <HH:MM>] [--battery <0-100>] [--wifi <0-3>] [--clear]` | Override status bar for clean screenshots |
| `simemu biometrics <slug> match\|fail` | Simulate Face ID / fingerprint result |

#### 📐 Percentage coordinates (`--pct`)

Use fractions of screen size instead of calculating device-specific pixel coordinates. Eliminates scaling errors across different device resolutions:

```bash
simemu tap        myapp-ios 0.5 0.92 --pct          # bottom-centre tap, any device
simemu swipe      myapp-ios 0.5 0.75 0.5 0.25 --pct # swipe up
simemu long-press myapp-android 0.5 0.5 --pct       # dead centre
```

### Shared-desktop workflow

On a Mac that a human is actively using, interactive iOS gestures should start with:

```bash
simemu present myapp-ios
simemu stabilize myapp-ios --json
simemu tap myapp-ios 0.5 0.92 --pct
```

`simemu tap`, `swipe`, and `long-press` now wait briefly for desktop idle, restore the previous frontmost app afterwards, and avoid acting against stale simulator bounds.

During focus-sensitive iOS interaction, `simemu` can also show:

- a macOS notification when it needs the desktop to go idle
- a small topmost HUD while it is briefly using Simulator

Disable the HUD with:

```bash
export SIMEMU_HUD=0
```

### 📡 Device state

| Command | Description |
|---|---|
| `simemu network <slug> airplane\|all\|wifi\|data\|none` | Set network mode (Android) |
| `simemu battery <slug> [--level <0-100>] [--reset]` | Override battery display (Android) |
| `simemu location <slug> <lat> <lng>` | Set GPS location override |
| `simemu location <slug> --clear` | Clear GPS override (iOS) |
| `simemu privacy <slug> grant\|revoke\|reset <bundle> <permission>` | Grant or revoke app permission |
| `simemu animations <slug> on\|off` | Enable/disable UI animations (`off` = stable Maestro flows) |

### 📦 App management

| Command | Description |
|---|---|
| `simemu install <slug> <path> [--timeout <sec>]` | Install `.app`/`.ipa` (iOS) or `.apk` (Android). Aborts with reboot hint if hung. Default: 120s |
| `simemu launch <slug> <bundle\|package> [args…]` | Launch app |
| `simemu terminate <slug> <bundle\|package>` | Force-stop app |
| `simemu uninstall <slug> <bundle\|package>` | Remove app |
| `simemu apps <slug> [--json]` | List installed apps |
| `simemu clear-data <slug> <package>` | Clear app data (Android) |
| `simemu reset-app <slug> <bundle\|package> [--no-launch]` | Force-stop + clear data + relaunch in one command |

#### ♻️ `reset-app`

```bash
simemu reset-app myapp-android com.example.myapp   # terminate + clear + relaunch
simemu reset-app myapp-ios     com.example.myapp   # wipes iOS data container, relaunches
simemu reset-app myapp-ios     com.example.myapp --no-launch  # clear only
```

### 📷 Capture

| Command | Description |
|---|---|
| `simemu screenshot <slug> [-o <path>] [--max-size <px>] [--format png\|jpeg\|…] [--json]` | Take a screenshot |
| `simemu compare <slug-a> <slug-b> [-o <path>] [--max-size <px>] [--json]` | Screenshot two simulators side by side |
| `simemu record start <slug> [-o <path>] [--codec hevc\|h264] [--json]` | Start screen recording |
| `simemu record stop <slug> [--json]` | Stop recording, print output path |
| `simemu log <slug> [--predicate <pred>] [--tag <tag>] [--level <level>]` | Stream logs (Ctrl-C to stop) |
| `simemu crash-log <slug> [--bundle <id>] [--since <minutes>] [--json]` | Show most recent crash report |

```bash
# Always resize iOS screenshots for Claude vision API
simemu screenshot myapp-ios --max-size 1000 -o /tmp/ios.png
export SIMEMU_SCREENSHOT_MAX_SIZE=1000   # or set globally

# Side-by-side iOS vs Android (requires: brew install imagemagick)
simemu compare myapp-ios myapp-android --max-size 1000 -o /tmp/compare.png

# Crash logs
simemu crash-log myapp-ios                                             # any crash, last hour
simemu crash-log myapp-android --bundle com.example.myapp --since 30  # filtered, 30 min
```

iOS crash logs: `~/Library/Logs/DiagnosticReports` (`.crash`/`.ips` files).
Android crash logs: logcat filtered for `FATAL EXCEPTION` and ANR events.

### 🔗 Deep links & files

| Command | Description |
|---|---|
| `simemu url <slug> <url>` | Open URL / deep link |
| `simemu push <slug> <local> <remote>` | Push file to Android emulator |
| `simemu pull <slug> <remote> <local>` | Pull file from Android emulator |
| `simemu add-media <slug> <file>` | Add photo/video to Photos/Gallery (iOS + Android) |
| `simemu push-notification <slug> <bundle-id> <payload.json>` | Send push notification (iOS) |
| `simemu clipboard <slug>` | Read the clipboard (iOS) |

### 🎭 Maestro integration

```bash
simemu maestro myapp-ios     flow.yaml                    # resolves UDID automatically
simemu maestro myapp-android flow.yaml                    # resolves ADB serial automatically
simemu maestro myapp-ios     login.yaml onboarding.yaml   # multiple flows
simemu maestro myapp-ios     flow.yaml --format junit     # extra Maestro flags
```

Use `simemu screenshot` to capture results — not Maestro's `takeScreenshot` (it ignores `SIMEMU_OUTPUT_DIR`).

### 🛠️ Create simulators

```bash
simemu create ios --list-devices
simemu create ios "My iPhone 16 Pro" --device "iPhone 16 Pro" --os "iOS 18"

simemu create android --list-images
simemu create android My_Pixel_API35 --api 35 --device pixel_6

simemu create genymotion "My Galaxy S24" --hwprofile "Samsung Galaxy S24" --osimage "14.0"
```

### 💤 Daemon & idle shutdown

```bash
simemu daemon install               # install as launchd LaunchAgent (starts at login)
simemu daemon uninstall
simemu daemon status
simemu serve --idle-timeout 30      # run server manually in foreground
simemu idle-shutdown --after 20     # one-shot, no daemon required
```

Normal `simemu` CLI usage now auto-starts a background API server if nothing is listening on `127.0.0.1:8765`.

Disable autostart when needed:

```bash
simemu --no-autostart status
SIMEMU_NO_AUTOSTART=1 simemu status
SIMEMU_AUTOSTART=0 simemu status
```

---

## 📸 Screenshot-as-proof workflow

Agents must **prove** UI changes with screenshots — never describe what they assume is on screen.

```bash
simemu screenshot myapp-ios     --max-size 1000 -o /tmp/ios_<feature>.png
simemu screenshot myapp-android --max-size 1000 -o /tmp/and_<feature>.png
simemu compare    myapp-ios myapp-android --max-size 1000 -o /tmp/compare.png
```

iOS taps, swipes, and long-presses are injected directly into the Simulator process — the system cursor does not move. Agents interact with simulators while you use your Mac normally.

---

## 🖥️ Headless mode

Android emulators boot headless (`-no-window`) by default, saving **300–700 MB of GPU/renderer memory** while keeping the framebuffer intact for screenshots.

```bash
simemu acquire android myapp-android --device "..." --window   # only when you need to watch
```

---

## 💤 Idle shutdown daemon

Shuts down simulators idle for more than N minutes without releasing reservations.

```bash
simemu daemon install              # default 20-minute timeout
simemu serve --idle-timeout 30     # or run manually
tail -f /tmp/simemu/daemon.log
```

---

## 🤖 Genymotion support

UUID-format `sim_id` → Genymotion backend automatically. Standard AVD names → AVD backend. TCP ADB connections established automatically when needed.

---

## 🛡️ Restricting agents to simemu only

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

## 🗂️ Adding simemu to a project

See **[docs/adding-to-project.md](docs/adding-to-project.md)** for the full onboarding guide.

### CLAUDE.md template

````markdown
## 🦤 Simulator / Emulator Management (simemu)

**simemu is the ONLY permitted way to interact with iOS simulators and Android emulators.**
A global hook blocks all direct `xcrun simctl`, `adb`, `emulator`, and `avdmanager` calls.

| Slug | Platform | Device |
|------|----------|--------|
| `myapp-ios` | iOS | Soba iPhone16 6.1in iOS18 |
| `myapp-android` | Android | Biscuit MedPhone 6.3in API35 |

```bash
export SIMEMU_AGENT=myapp

# Acquire (permanent — do not release between sessions)
simemu acquire ios     myapp-ios     --device "Soba iPhone16 6.1in iOS18"    --wait 120
simemu acquire android myapp-android --device "Biscuit MedPhone 6.3in API35" --wait 120

# Install & launch
simemu install myapp-ios     path/to/App.app --timeout 60
simemu install myapp-android path/to/app.apk --timeout 60
simemu launch  myapp-ios     com.example.myapp
simemu launch  myapp-android com.example.myapp

# PROVE DELIVERABLES WITH SCREENSHOTS — never verbal claims
simemu screenshot myapp-ios     --max-size 1000 -o /tmp/ios_<feature>.png
simemu screenshot myapp-android --max-size 1000 -o /tmp/and_<feature>.png
simemu compare myapp-ios myapp-android --max-size 1000 -o /tmp/compare.png

# Gestures — use --pct for device-agnostic coordinates (0.0–1.0)
simemu tap        myapp-ios 0.5 0.92 --pct
simemu swipe      myapp-ios 0.5 0.75 0.5 0.25 --pct
simemu long-press myapp-ios 0.5 0.5  --pct
simemu rotate     myapp-ios landscape
simemu key        myapp-ios home

# App state
simemu reset-app myapp-ios     com.example.myapp
simemu reset-app myapp-android com.example.myapp
simemu crash-log myapp-ios     --since 30
simemu crash-log myapp-android --bundle com.example.myapp

# Diagnostics
simemu check  myapp-ios --bundle com.example.myapp
simemu log    myapp-ios
simemu reboot myapp-ios

# Device state
simemu appearance myapp-ios dark
simemu status-bar myapp-ios --time "9:41" --battery 100 --wifi 3
simemu status-bar myapp-ios --clear
simemu biometrics myapp-ios match
simemu privacy    myapp-ios grant com.example.myapp camera
simemu network    myapp-android airplane
simemu battery    myapp-android --level 85
```

**Rules:**
- NEVER call `xcrun simctl` or `adb` directly — the hook blocks it
- Use `--pct` for all tap/swipe/long-press — eliminates device-specific scaling bugs
- Always screenshot to prove UI state before committing
- If `--wait` times out, tell the user; do not proceed blindly
````

---

## 🌍 Environment variables

| Variable | Default | Description |
|---|---|---|
| `SIMEMU_AGENT` | `pid-<PID>` | Agent identity shown in `simemu status` |
| `SIMEMU_OUTPUT_DIR` | `~/.simemu/` | Where screenshots and recordings are saved |
| `SIMEMU_SCREENSHOT_MAX_SIZE` | — | Auto-resize screenshots to this pixel limit |
| `SIMEMU_IDLE_TIMEOUT` | `20` | Minutes before idle-shutdown (daemon) |

---

## ⚙️ How it works

**State & locking** — All reservation state lives in `/tmp/simemu/state.json`, protected by an exclusive `fcntl` file lock held only during state reads/writes. Multiple agents can run concurrently.

**Permanent reservations** — Reservations never expire by design. Any auto-reclaim strategy caused agents to steal each other's simulators mid-session. Every proxy command updates `heartbeat_at` for idle-shutdown tracking.

**Install timeout** — `simemu install` uses a subprocess timeout instead of blocking forever. On timeout it raises a descriptive error with a `simemu reboot <slug>` suggestion.

**Percentage coordinates** — `--pct` converts 0.0–1.0 fractions to physical pixels at call time. iOS uses a device lookup table (logical point space); Android queries `wm size`.

**IPA installation** — `.ipa` files are automatically extracted; the `.app` bundle is installed via `xcrun simctl install` and the temp dir is cleaned up transparently.

**Android serial resolution** — AVD names are decoupled from ADB serials. The serial is resolved dynamically at each call by querying running emulators.

**Genymotion routing** — UUID-format `sim_id` → Genymotion backend. Standard names → AVD backend.

---

## 📄 State file reference

`/tmp/simemu/state.json` (cleared on reboot):

```json
{
  "allocations": {
    "myapp-ios": {
      "slug": "myapp-ios",
      "sim_id": "A1B2C3D4-0000-0000-0000-ABCDEF012345",
      "platform": "ios",
      "device_name": "Soba iPhone16 6.1in iOS18",
      "agent": "myapp",
      "acquired_at": "2025-01-15T10:23:00+00:00",
      "heartbeat_at": "2025-01-15T10:24:30+00:00",
      "recording_pid": null,
      "recording_output": null
    }
  }
}
```

---

## 🦤 Version history

**0.1.0** — Initial release.

- Named reservation system with permanent slugs
- iOS (xcrun simctl) and Android (adb/emulator) platform support
- Genymotion Desktop integration
- Full gesture proxy: tap, swipe, long-press, rotate, key events
- `--pct` flag for device-agnostic percentage coordinates
- `reset-app`: force-stop + clear + relaunch in one command
- `crash-log`: pull iOS DiagnosticReports or Android logcat crashes
- `compare`: composite side-by-side screenshot of two simulators
- `check`: verify simulator is booted and app is in foreground
- `install --timeout`: abort with reboot hint instead of hanging
- Screen recording, log streaming, Maestro integration
- Idle-shutdown daemon (launchd LaunchAgent)
- Guard hook blocking direct xcrun simctl / adb calls

---

## 📜 License

MIT
