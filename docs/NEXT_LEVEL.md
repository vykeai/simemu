# simemu v2 — Complete Command Status

All commands from the original v3 roadmap have been implemented and are available via `simemu do $SESSION <command>`.

---

## Implemented APIs (all via `simemu do <session>`)

### System Alerts & Dialogs -- DONE

```bash
simemu do $S dismiss-alert                    # dismiss any visible system alert
simemu do $S accept-alert                     # tap "Allow" / "OK" on system alert
simemu do $S deny-alert                       # tap "Don't Allow" / "Cancel"
simemu do $S auto-dismiss                     # reset privacy warnings + disable animations
```

### Auto-Grant All Permissions -- DONE

```bash
simemu do $S grant-all <bundle-id>            # grant all permissions preemptively
```

iOS: `xcrun simctl privacy grant all`. Android: grants camera, audio, location, contacts, storage, media, notifications.

### Deeplink Testing -- DONE

```bash
simemu do $S url "myapp://screen/profile"                 # open deep link
simemu do $S deeplink-proof "myapp://screen" -o proof.png # open + wait 3s + screenshot
simemu do $S wait-for-render 3 -o proof.png               # wait + screenshot (no URL)
```

### Clipboard -- DONE

```bash
simemu do $S clipboard-set "paste this text"
simemu do $S clipboard-get                                # iOS only
```

### App Data Management -- DONE

```bash
simemu do $S clear-data <bundle-id>           # wipe app data (Android; iOS terminates only)
simemu do $S reset-app <bundle> <app-path>    # uninstall + reinstall + relaunch
simemu do $S app-container <bundle>           # get app data container path
```

### Network Simulation -- DONE

```bash
simemu do $S network offline                  # disable wifi + data (Android)
simemu do $S network slow                     # partial throttle (Android)
simemu do $S network normal                   # restore (Android)
```

iOS network simulation is not supported via simctl (requires Network Link Conditioner).

### Keychain -- DONE

```bash
simemu do $S keychain-reset                   # clear keychain (iOS only)
```

### iCloud Sync -- DONE

```bash
simemu do $S icloud-sync                      # trigger iCloud sync (iOS only)
```

### App Info -- DONE

```bash
simemu do $S app-info <bundle-id>             # version, permissions, data size
simemu do $S is-running <bundle-id>           # check if process is running
simemu do $S foreground-app                   # get foreground app bundle ID
```

### Accessibility -- DONE

```bash
simemu do $S a11y-tree                        # dump accessibility tree (Android only)
simemu do $S a11y-tap "Login Button"          # tap by label — headless via Maestro
```

### Device Sensors & State -- DONE

```bash
simemu do $S shake                            # shake gesture
simemu do $S rotate landscape                 # orientation
simemu do $S appearance dark                  # light/dark mode
simemu do $S location 37.7 -122.4             # GPS override
simemu do $S status-bar --time 9:41           # status bar override
simemu do $S font-size large                  # font scale (Android)
simemu do $S reduce-motion on                 # disable animations (Android)
```

### Navigation -- DONE

```bash
simemu do $S scroll down                      # scroll up/down/left/right
simemu do $S back                             # go back (edge swipe iOS, key Android)
simemu do $S home                             # home screen
simemu do $S type-submit "search query"       # type text + press enter
```

### Video Recording -- DONE

```bash
simemu do $S video-start -o recording.mp4     # start recording
simemu do $S video-stop <pid>                 # stop recording
```

### Additional Commands -- DONE

```bash
simemu do $S clone                            # clone iOS simulator
simemu do $S contacts-import contacts.vcf     # import contacts
simemu do $S notifications-clear              # clear notifications (Android)
simemu do $S siri "query"                     # Siri invocation (limited)
simemu do $S build                            # build app via keel config
simemu do $S env                              # device info (UDID/serial/OS)
```

---

## Future

### macOS Platform Support

Add `simemu claim macos` for testing macOS apps. No simulator needed — runs directly on the host. Would need:
- Window management for macOS app windows
- Screenshot capture via screencapture
- Accessibility-based interaction via AppleScript/AXUIElement
- App lifecycle management via launchctl/open/kill

### Biometrics via Session API

`biometrics match|fail` exists in the legacy CLI but has not been ported to the v2 `do` command dispatcher. Needs:
- iOS: `xcrun simctl io <udid> sendkey` or Notifyutil for Face ID
- Android: fingerprint injection via emulator console

### iOS Accessibility Tree

`a11y-tree` currently only works on Android (via uiautomator dump). iOS support would require:
- XCUITest bridge or Maestro hierarchy dump
- Parsing the accessibility hierarchy into a usable format

### iOS Network Simulation

`network` command only works on Android. iOS would need:
- Network Link Conditioner profile installation
- Or custom proxy configuration via simctl

### iOS Font Size / Reduce Motion

`font-size` and `reduce-motion` only work on Android. iOS would need:
- Writing to simulator preferences plist
- Or Accessibility Inspector bridge

### Real Device Parity

Expand `--real` device support beyond install/launch/screenshot to cover all commands.

### Multi-Device Orchestration

Coordinate commands across multiple sessions for testing cross-device features (messaging, sharing, handoff).
