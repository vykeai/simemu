# Adding simemu to a project

This is the minimal set of steps to give a Claude agent its own simulator.

---

## For the project owner (you)

### 1. Pick a slug and reserve it

```bash
# See what's free
simemu list ios
simemu list android

# iOS — acquire with a descriptive slug
simemu acquire ios myapp-ios --device "Soba iPhone16 6.1in iOS18" --wait 120

# Android — headless by default (no window, saves RAM)
simemu acquire android myapp-android --device "Biscuit MedPhone 6.3in API35" --wait 120
```

These **primary slugs** are permanent — they stay reserved until you run `simemu release <slug>`.

### 2. Add a section to the project's CLAUDE.md (or AGENTS.md)

Paste this template and fill in the table:

```markdown
## Simulator / Emulator Management (simemu)

**simemu is the ONLY permitted way to interact with iOS simulators and Android emulators.**
`simemu` is installed at `~/dev/simemu`. A global hook blocks all direct `xcrun simctl`, `adb`, `emulator`, and `avdmanager` calls.

### Your assigned simulators

| Slug | Platform | Device |
|------|----------|--------|
| `myapp-ios` | iOS | Soba iPhone16 6.1in iOS18 |
| `myapp-android` | Android | Biscuit MedPhone 6.3in API35 |

These are **permanently reserved for this project** — do not release them.

### Agent identity (required every session)

Before any simemu command, export your project name as your identity:

```bash
export SIMEMU_AGENT=myapp     # just the project name — short, consistent, memorable
```

This is what allows you to release slugs you acquired in a previous session. Without it, simemu uses `pid-XXXX` (your process ID) as your identity — and when the process exits, nobody can release those slugs without knowing the original PID.

`simemu status` shows the agent column — this is how you see at a glance which project owns what.

### Using your simulators

\`\`\`bash
simemu list ios
simemu list android

# Acquire with descriptive slug: {project}-{purpose}
simemu acquire ios myapp-ios --device "Soba iPhone16 6.1in iOS18" --wait 120
simemu acquire android myapp-android --device "Biscuit MedPhone 6.3in API35" --wait 120

# ── Headless by default ────────────────────────────────────────────────────
# Android boots without a window. iOS window stays behind other apps.
# Add --window to acquire/boot only when you need to watch directly:
#   simemu acquire android myapp-android --device "..." --wait 120 --window
# Save all screenshots under ~/Desktop/screenshots/{project-name}/:
#   export PROJECT_SCREENSHOT_DIR=~/Desktop/screenshots/$(basename "$(git rev-parse --show-toplevel)")
#   mkdir -p "$PROJECT_SCREENSHOT_DIR"
# PROVE DELIVERABLES WITH SCREENSHOTS — never verbal claims:
#   simemu screenshot myapp-ios     -o "$PROJECT_SCREENSHOT_DIR/ios_<feature>.png"
#   simemu screenshot myapp-android -o "$PROJECT_SCREENSHOT_DIR/and_<feature>.png"
#   (read both files to verify visually before committing)
# ───────────────────────────────────────────────────────────────────────────

# Install and launch
simemu install myapp-ios     path/to/App.app
simemu launch  myapp-ios     com.example.myapp
simemu install myapp-android path/to/app.apk
simemu launch  myapp-android com.example.myapp
simemu clear-data myapp-android com.example.myapp   # reset app data (Android)

# Screenshot
simemu screenshot myapp-ios     -o "$PROJECT_SCREENSHOT_DIR/ios_<feature>.png"
simemu screenshot myapp-android -o "$PROJECT_SCREENSHOT_DIR/and_<feature>.png"

# UI interaction
simemu tap        myapp-ios 195 400
simemu swipe      myapp-ios 195 700 195 200          # swipe up
simemu long-press myapp-ios 195 400
simemu rotate     myapp-ios landscape
simemu appearance myapp-ios dark
simemu key        myapp-ios home
simemu biometrics myapp-ios match

# Status bar (for clean screenshots)
simemu status-bar myapp-ios --time "9:41" --battery 100 --wifi 3
simemu status-bar myapp-ios --clear

# Device state
simemu reboot     myapp-ios
simemu reboot     myapp-android
simemu network    myapp-android airplane             # airplane | all | wifi | data | none
simemu battery    myapp-android --level 85           # fake battery % for screenshots
simemu battery    myapp-android --reset              # restore real battery
simemu privacy    myapp-ios grant com.example.myapp camera
simemu location   myapp-ios 37.7749 -122.4194

# Logs
simemu log        myapp-ios
simemu log        myapp-android

# Inspect / check state
simemu env    myapp-ios       # → udid, name, state, runtime, screen_width_pt, screen_height_pt
simemu env    myapp-android   # → udid, name, state, api_level, serial
simemu status
\`\`\`

### Acquiring extra simulators when needed

If you need a simulator not in the table above (e.g. a different OS version, tablet, large screen),
you may acquire one from the free pool:

\`\`\`bash
simemu list ios              # see what's free
simemu list android

# Acquire with a descriptive slug: {project}-{purpose}
simemu acquire ios myapp-ios26 --device "Churro iPhone17 6.3in iOS26" --wait 120
simemu acquire android myapp-tablet --device "Churro Tablet 10.1in API35" --wait 120

# ... do your testing ...

# Always release extra slugs when done
simemu release myapp-ios26
simemu release myapp-tablet
\`\`\`

Name extra slugs `{project}-{purpose}` so they're identifiable in `simemu status`.
Use `--wait 120` so you queue up rather than fail immediately if everything is busy.

**Rules — no exceptions:**
- **NEVER** call `xcrun simctl` or `adb` directly — the hook will block it
- **NEVER** release your permanent primary slugs (`myapp-ios`, `myapp-android`)
- **ALWAYS** release extra/temporary slugs when your test is done
- **ALWAYS** prove UI changes with screenshots — never verbal claims
- If `simemu list` shows nothing free and `--wait` times out, tell the user
```

That's it. The agent will read this at session start and know exactly which simulators to use.

---

## For the agent

If you are an agent reading this:

1. **Export your agent identity first, every session:**
   ```bash
   export SIMEMU_AGENT=myapp   # use the project name from your CLAUDE.md
   ```
2. Your **permanent** slugs are listed in `### Your assigned simulators`. Use them freely — they are always yours.
3. For testing on a **different device or OS**, check `simemu list` for free simulators and acquire one temporarily using the `{project}-{purpose}` naming pattern.
4. **Release extra slugs when done** — but never release your permanent ones.
   - Release only works because your `SIMEMU_AGENT` matches the one used at acquire time.
   - If release fails saying "reserved by agent pid-XXXX", you forgot to export `SIMEMU_AGENT`.
5. Never call `xcrun simctl` or `adb` — the hook will block it.
6. **Never release another project's slugs** — even if they appear free or idle.
7. **Prove all UI work with screenshots** — take a screenshot after every significant UI change and read the file to verify before reporting done.
8. Save proof screenshots under `~/Desktop/screenshots/{project-name}/` so they persist beyond the session.

---

## Managing reservations

```bash
simemu status                    # see all active reservations
simemu release myapp-ios26       # free a temporary slug
simemu rename myapp-ios newslug  # rename a slug
simemu delete myapp-ios          # delete the physical simulator + release
```
