# simemu

## Screenshot Storage

Save every screenshot to `~/Desktop/screenshots/{project-name}/`.
Use the git repo name for `{project-name}`:

```bash
export PROJECT_SCREENSHOT_DIR=~/Desktop/screenshots/$(basename "$(git rev-parse --show-toplevel)")
mkdir -p "$PROJECT_SCREENSHOT_DIR"
```

Do not keep proof or review screenshots in `/tmp`.

## What This Is
`simemu` is the simulator allocation manager for multi-agent iOS and Android development. It assigns named slugs to reserved devices, proxies simulator/emulator operations through one CLI, and prevents direct `xcrun simctl` / `adb` usage outside the tool.

## Tech Stack
Python 3.11+, setuptools, optional FastAPI/uvicorn server components

## Key Commands
- `pip3 install -e .` — install the CLI locally
- `simemu --version` — verify the CLI is available
- `simemu list ios` — show available iOS simulators
- `simemu list android` — show available Android emulators

## Conventions
- CLI entrypoint lives in `simemu/cli.py`
- Platform-specific behavior stays in `simemu/ios.py` and `simemu/android.py`
- Reservation and state logic must stay consistent across CLI commands
- Slugs, agent identity, and JSON output shape are part of the tool contract

## Architecture Notes
- `simemu` is infrastructure for other repos, not a product-specific app
- Backwards compatibility matters because downstream projects embed exact `simemu` workflows in their agent docs
- Guard-hook assumptions in consuming repos depend on `simemu` remaining the only supported simulator interface

## Do Not
- Break slug reservation semantics without updating downstream tooling and docs
- Change command names or argument behavior casually
- Bypass the centralized state/reservation model in ad hoc scripts
- Introduce direct-project assumptions into the shared simulator layer

## CLI Rules
- Read commands should keep stable text/JSON output
- Errors must be explicit and non-zero
- Cross-platform behavior should be consistent where the command surface is shared
- Simulator screenshots are delivery evidence, so capture-related commands must stay reliable

## Definition of Done
- [ ] `pip3 install -e .` works
- [ ] `simemu --help` output is accurate
- [ ] Changed commands are tested manually or automatically on the relevant platform
- [ ] Any contract changes are reflected in downstream docs
- [ ] Changes committed AND pushed
