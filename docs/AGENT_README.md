## Simulators — simemu

All simulator/emulator access goes through simemu. Never call xcrun simctl or adb directly.

### Get a device

```bash
SESSION=$(simemu claim ios | jq -r .session)
SESSION=$(simemu claim android | jq -r .session)

# Options:
#   --version 26          specific OS version
#   --form-factor ipad    phone (default), tablet, watch, tv, vision
#   --real                prefer real device over simulator
#   --label "my task"     label for tracking
```

### Use it

```bash
simemu do $SESSION install ./path/to/app.ipa
simemu do $SESSION launch com.example.app
simemu do $SESSION tap 250 500
simemu do $SESSION screenshot -o proof.png
simemu do $SESSION maestro flow.yaml
simemu do $SESSION url "myapp://deep/link"
```

### If a command fails with "session_expired"

The error message includes the exact re-claim command. Just run it:

```bash
SESSION=$(simemu claim ios --version 26 --form-factor phone | jq -r .session)
```

### Keep session alive during long waits

```bash
simemu do $SESSION renew
```

### When done

```bash
simemu do $SESSION done
```

### Rules

- **ONE command**: `simemu claim` to get a device, `simemu do` to use it
- **NEVER** call `xcrun simctl`, `adb`, `emulator`, or `simctl` directly
- **NEVER** manage device lifecycle (boot/shutdown) — simemu does this
- If something breaks, re-claim — simemu handles recovery
- Sessions expire after inactivity — the error tells you what to do
