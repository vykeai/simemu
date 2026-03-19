# simemu v3 — Complete Mobile Engineering Platform

## Vision
simemu should be the ONLY tool agents need for mobile app development, testing, and proof capture. No agent should ever need to know about xcrun simctl, adb, Xcode, or Android Studio.

---

## New APIs to expose via `simemu do <session>`

### System Alerts & Dialogs (HIGH PRIORITY)
Agents constantly get blocked by system alerts they can't dismiss.

```bash
simemu do $S dismiss-alert                    # dismiss any visible system alert
simemu do $S accept-alert                     # tap "Allow" / "OK" on system alert
simemu do $S deny-alert                       # tap "Don't Allow" / "Cancel"
```

Implementation:
- iOS: `xcrun simctl ui <udid> alert` or Maestro's auto-dismiss
- Android: `adb shell am broadcast` or `adb shell input keyevent KEYCODE_ENTER`
- Could also auto-dismiss common alerts (location, notifications, tracking) on boot

### Auto-Grant All Permissions on Boot
```bash
simemu do $S grant-all <bundle-id>            # grant all permissions preemptively
```
- iOS: `xcrun simctl privacy <udid> grant all <bundle-id>`
- Android: `adb shell pm grant <package> <permission>` for each common permission

### Deeplink Testing
```bash
simemu do $S deeplink "myapp://screen/profile?id=123"
simemu do $S deeplink --wait-for-render 3     # open + wait + screenshot
```
Already exists as `url` but could be smarter:
- Wait for render after opening
- Auto-screenshot after deeplink
- Verify the deeplink was handled (check foreground app)

### Clipboard
```bash
simemu do $S clipboard-set "paste this text"
simemu do $S clipboard-get
```
- iOS: `xcrun simctl pbcopy/pbpaste`
- Android: Already works via input

### App Data Management
```bash
simemu do $S clear-data <bundle-id>           # wipe app data
simemu do $S export-data <bundle-id> -o ./    # export app container
simemu do $S import-data <bundle-id> ./data   # import app data
```
- iOS: `xcrun simctl get_app_container` + file ops
- Android: `adb shell pm clear` / `adb pull/push`

### Network Simulation
```bash
simemu do $S network offline                  # airplane mode
simemu do $S network slow                     # throttle to 3G speeds
simemu do $S network normal                   # restore
```
- iOS: Network Link Conditioner profile
- Android: `adb shell svc wifi/data`

### Keychain
```bash
simemu do $S keychain reset                   # clear keychain
```
- iOS: `xcrun simctl keychain <udid> reset`

### iCloud Sync
```bash
simemu do $S icloud-sync
```
- iOS: `xcrun simctl icloud_sync <udid>`

### App Info
```bash
simemu do $S app-info <bundle-id>             # version, data size, permissions
```
- iOS: `xcrun simctl appinfo`
- Android: `adb shell dumpsys package`

### Accessibility
```bash
simemu do $S a11y-tree                        # dump accessibility tree
simemu do $S a11y-tap "Login Button"          # tap by accessibility label
```
- iOS: Could use accessibility inspector bridge
- Android: `adb shell uiautomator dump`

### Device Sensors
```bash
simemu do $S shake                            # already exists
simemu do $S battery 20                       # set battery level
simemu do $S orientation landscape            # already exists as rotate
```

---

## Skills for Claude Code

### /mobile-boot
Boot simulator, install app, launch, grant permissions, take proof screenshot — all in one.

### /mobile-proof
Screenshot + resize + save to project screenshot dir. The standard proof workflow.

### /mobile-deeplink
Open deeplink, wait for render, screenshot. For testing navigation flows.

### /mobile-reset
Clear app data, reinstall, relaunch. Clean slate for testing.

### /mobile-flow
Run a Maestro flow and capture results.

### /mobile-compare
Screenshot iOS + Android side by side for cross-platform comparison.

---

## Headless Tap Solution

Current limitation: iOS tap/swipe needs a visible window (System Events click).

Better approach: Use `xcrun simctl io <udid> enumerate` to find the device's framebuffer, then inject touch events via `simctl` private APIs or Maestro's touch injection.

Alternative: Wrap all tap operations in single-step Maestro flows:
```yaml
- tapOn:
    point: "250,500"
```
This works fully headless since Maestro connects via UDID.

---

## Agent README v3

```markdown
## Simulators — simemu

### Get a device (always hidden — no windows on screen)
SESSION=$(simemu claim ios | jq -r .session)

### Use it
simemu do $SESSION install ./app.ipa
simemu do $SESSION launch com.app.id
simemu do $SESSION grant-all com.app.id       # no permission dialogs
simemu do $SESSION deeplink "app://screen"
simemu do $SESSION screenshot -o proof.png
simemu do $SESSION maestro flow.yaml
simemu do $SESSION done

### If something breaks
# Error message includes exact re-claim command
SESSION=$(simemu claim ios --version 26 | jq -r .session)

### Rules
- NEVER call xcrun simctl, adb, or emulator directly
- NEVER worry about windows/displays — everything is hidden
- Use Maestro for UI interaction, not raw taps
- Sessions expire after inactivity — error tells you what to do
```
