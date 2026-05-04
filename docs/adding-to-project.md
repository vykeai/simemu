# Adding simemu to a project

This is the minimal setup to give a Claude agent its own simulator or emulator
using the current session-based API.

---

## For the project owner

### 1. Verify a session can be claimed

```bash
SESSION_IOS=$(simemu claim ios --form-factor phone --label "myapp ios" | jq -r .session)
SESSION_ANDROID=$(simemu claim android --form-factor phone --label "myapp android" | jq -r .session)

simemu do "$SESSION_IOS" done
simemu do "$SESSION_ANDROID" done
```

### 2. Add a section to the project's `CLAUDE.md` or `AGENTS.md`

Paste this template and adjust the project name, build commands, and package IDs:

```markdown
## Simulator / Emulator Management (simemu)

**simemu is the ONLY permitted way to interact with iOS simulators and Android emulators.**
`simemu` is installed at `~/dev/simemu`. A global hook blocks direct `xcrun simctl`,
`adb`, `emulator`, and `avdmanager` calls.

### Agent identity

Before any simemu command, export your project name as your identity:

```bash
export SIMEMU_AGENT=myapp
export PROJECT_SCREENSHOT_DIR=~/Desktop/screenshots/myapp
mkdir -p "$PROJECT_SCREENSHOT_DIR"
```

Use the same `SIMEMU_AGENT` value every session so active sessions are clearly attributable in
`simemu sessions`.

### Core flow

```bash
SESSION_IOS=$(simemu claim ios --form-factor phone --label "myapp ios" | jq -r .session)
SESSION_ANDROID=$(simemu claim android --form-factor phone --label "myapp android" | jq -r .session)

simemu do "$SESSION_IOS" install path/to/App.app
simemu do "$SESSION_IOS" launch com.example.myapp

simemu do "$SESSION_ANDROID" install path/to/app.apk
simemu do "$SESSION_ANDROID" launch com.example.myapp
simemu do "$SESSION_ANDROID" clean-retry com.example.myapp   # use when stale app state traps the shell

simemu do "$SESSION_IOS" screenshot -o "$PROJECT_SCREENSHOT_DIR/ios_feature.png"
simemu do "$SESSION_ANDROID" screenshot -o "$PROJECT_SCREENSHOT_DIR/android_feature.png"

simemu do "$SESSION_IOS" done
simemu do "$SESSION_ANDROID" done
```

### Useful commands

```bash
simemu sessions
simemu sessions --json

simemu do "$SESSION_IOS" env
simemu do "$SESSION_ANDROID" env

simemu do "$SESSION_IOS" tap 195 400
simemu do "$SESSION_IOS" swipe 195 700 195 200
simemu do "$SESSION_IOS" long-press 195 400
simemu do "$SESSION_IOS" appearance dark
simemu do "$SESSION_IOS" status-bar --time "9:41" --battery 100 --wifi 3
simemu do "$SESSION_IOS" status-bar --clear

simemu do "$SESSION_ANDROID" clear-data com.example.myapp
simemu do "$SESSION_ANDROID" reboot
simemu do "$SESSION_IOS" reboot
```

### Claiming a different device

```bash
SESSION_TABLET=$(simemu claim android --form-factor tablet --label "myapp tablet" | jq -r .session)
SESSION_IOS26=$(simemu claim ios --version 26 --form-factor phone --label "myapp ios26" | jq -r .session)

# ... do testing ...

simemu do "$SESSION_TABLET" done
simemu do "$SESSION_IOS26" done
```

### Rules

- **NEVER** call `xcrun simctl` or `adb` directly
- **ALWAYS** use `simemu claim` to get a device and `simemu do` to interact with it
- **ALWAYS** release temporary sessions with `simemu do <session> done`
- **ALWAYS** save proof screenshots to `~/Desktop/screenshots/{project}/`
- If `simemu claim` cannot find a matching device, report the exact filter that failed
```

---

## For the agent

If you are an agent reading this:

1. Export `SIMEMU_AGENT` and `PROJECT_SCREENSHOT_DIR` first.
2. Claim a session with `simemu claim <platform>`.
3. Store the returned session ID and use `simemu do <session> ...` for all work.
4. If Android lands in stale app state, prefer `simemu do <session> clean-retry <package>`.
5. Save proof to `~/Desktop/screenshots/{project}/`, never `/tmp`.
6. Release finished sessions with `simemu do <session> done`.
7. Never call `xcrun simctl` or `adb` directly.

---

## Managing sessions

```bash
simemu sessions
simemu do "$SESSION" done
```
