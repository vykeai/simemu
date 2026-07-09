# simemu

## One-liner
Session-based simulator and emulator allocation manager for multi-agent iOS and Android development — when multiple AI agents build features simultaneously, simemu hands each one an exclusive device session so they stop fighting over simulators, with all device operations funnelled through a single `simemu do` interface and direct `xcrun simctl` / `adb` calls blocked by a guard hook.

## Functionality / capabilities (current + PLANNED — the full picture)

### Core model
- **Session-based allocation**: agents call `simemu claim ios` / `simemu claim android` and receive an opaque session ID (e.g. `s-a7f3b2`) rather than a raw UDID or AVD name. The session manages the full device lifecycle automatically.
- **Exclusive claims**: a claim reserves the device for the claimant only. Two concurrent `claim ios` calls from different agents always get different UDIDs; if the device pool is exhausted, the second caller fails fast (non-zero exit) instead of silently sharing. `--wait <seconds>` blocks for a free device instead of failing.
- **Ownership enforcement**: each claim records the parent PID of the invoking shell plus an opaque `claim_token`. The session store (`~/.simemu/sessions.json`) is guarded by an `fcntl.flock` exclusive lock during every selection/save/reap. Stale claims (owner PID dead) are reaped automatically before new claims. Strict-ownership mode is opt-in via `SIMEMU_SESSION_TOKEN`.
- **Scoped destructive ops**: `shutdown`/`erase`/`boot` are scoped to the single session's UDID. No `--all` flag exists; simemu never runs `xcrun simctl shutdown all`-style sweeps that would nuke sibling-claimed devices.
- **Headless by default**: simulators run with no windows, no dock icons, no interruptions; `--show`/`show`/`hide` control visibility.

### Session lifecycle / resource management
- State machine: `ACTIVE` → (idle 20min) `IDLE` → (idle 40min more) `PARKED` (device shut down, session preserved) → (idle 2hr) `EXPIRED`. `do done` → `RELEASED` immediately. A `do` on a parked session re-boots and returns to active.
- **Memory budget**: configurable ceiling (default 16GB via `SIMEMU_MEMORY_BUDGET_MB`). Before booting a new device it checks total estimated memory; if over budget it parks lowest-priority idle sessions; if still over it returns an error with queue info.
- Session-expiry error messages include the exact re-claim command to run.
- Crash-safe atomic writes and corruption recovery for `sessions.json` and history logs; session-store schema versioning and migrations; auto-cleanup of expired sessions; session recovery after reboot (detects stale sessions on monitor startup).

### Device selection
- Form factors: phone (default), tablet, watch, tv, vision (`--form-factor`).
- OS version pinning (`--version 26`).
- Real-device preference (`--real`) and targeting by device id/name/alias (`--device`).
- **Permanent reservations**: per-product, slug-based device reservation pools (e.g. `sitches-ios`, `fitkind-ios`) with config-backed policy; reserved simulators behave like dedicated appliances.
- **Claim policy config**: aliases, defaults, per-product device preferences.
- **Device aliases / renaming**: persistent labels for real devices and simulators (`relabel`, `rename`), editable from the menubar UI.

### Device operations (all via `simemu do $SESSION <command>` — 50+ commands)
- **App lifecycle**: install (`.app`/`.ipa`/`.apk`), launch, terminate, uninstall, reset-app, clear-data, is-running, foreground-app, app-info, app-container, build (via keel/execution.yaml or `--raw`).
- **UI interaction**: a11y-tap (by accessibility label, works headless via Maestro), tap, swipe, long-press, scroll, back, home, type-submit, input, key, rotate, shake.
- **Permissions & alerts**: grant-all, dismiss-alert, accept-alert, deny-alert, auto-dismiss.
- **Capture & proof**: screenshot, deeplink-proof, wait-for-render, video-start/video-stop, log-crash. Outputs carry a `simemu.mobile-proof.v1` artifact envelope binding the artifact to lease/session, device, boot state, connection id, optional build artifact, timestamp, and SHA-256 file metadata — for Atlas, Sentinel, and Proofy automation clients.
- **Device state**: appearance (light/dark), status-bar override, location/GPS, network (offline/slow/normal, Android), keychain-reset (iOS), icloud-sync (iOS), font-size (Android), reduce-motion (Android), notifications-clear (Android).
- **Clipboard**: clipboard-set, clipboard-get (iOS).
- **Files & media**: url/deep-link, add-media, push/pull (Android), contacts-import.
- **Maestro integration**: `maestro <flow.yaml>` resolves device id automatically.
- **Accessibility**: a11y-tap, a11y-tree (Android dump).
- **Session mgmt**: done, boot, show, hide, renew, reboot, env, clone (iOS), siri (limited).

### Interfaces & deployment surface
- **CLI** (`simemu`, entry point `simemu.cli:main`), with shell completions.
- **HTTP API** auto-started on `127.0.0.1:8765`: `POST /v2/claim`, `POST /v2/do`, `GET /v2/sessions`, plus an orchestration **lease** layer (`POST/GET /v2/leases`, `DELETE /v2/leases/{id}`) carrying host/run-id/expiry metadata for distributed automation; exclusivity still enforced by the v2 session lock. Disable autostart with `SIMEMU_NO_AUTOSTART=1`.
- **macOS menubar app** (SimEmuBar, SwiftUI): shows active sessions, device names/labels, status (active/idle/parked), quick show/hide/release, per-tile context menu, hide-all/show-all, live refresh by watching `sessions.json`.
- **Monitor launchd agent** for lifecycle management + a watchdog that detects dead daemons and guides recovery.
- **Guard hook**: `~/.claude/simemu-guard.py` registered as a Claude Code `PreToolUse` Bash hook; blocks `xcrun simctl`, `xcrun xctrace`, `adb install/shell/logcat/pull/push/uninstall`, `emulator -avd`, `avdmanager create avd` (with heredoc/quote scrubbing) and tells the agent to use simemu instead.
- **fed integration**: registers with fed for service discovery across the network.
- **Scouty / desktop-lease integration**: `present` (canonical window placement), desktop lease coordination, brief focus acquisition with restore of the user's previous frontmost app — for shared-desktop reliability when a human and agents use the same Mac.
- **Trace export**: structured trace bundle export for transcript-backed debugging.
- **Runecode/doctor integration**: detects broken simemu setup and suggests fixes.
- **Project integration kit**: canonical `keel/execution.yaml` + AGENTS.md snippets for downstream repos.

### Planned / unrealized (from NEXT_LEVEL.md, PRODUCT_IT_WAS_MEANT_TO_BE.md, tasks)
- **macOS app testing**: `simemu claim macos` — host-native (no simulator), needing window mgmt, `screencapture`, AppleScript/AXUIElement interaction, launchctl/open/kill lifecycle. (Note: a macOS-platform task is marked done; full macOS app-testing parity is still framed as future.)
- **Biometrics via session API**: `biometrics match|fail` exists in the legacy CLI but not ported to the v2 `do` dispatcher (Face ID via simctl sendkey/Notifyutil; Android fingerprint injection).
- **iOS a11y-tree** (currently Android-only — needs XCUITest/Maestro hierarchy dump).
- **iOS network simulation** (Network Link Conditioner profile or proxy; Android-only today).
- **iOS font-size / reduce-motion** (simulator-preferences plist; Android-only today).
- **Real-device parity**: expand `--real` beyond install/launch/screenshot to all commands.
- **Multi-device orchestration**: coordinate commands across sessions for cross-device features (messaging, sharing, handoff) and test matrices ("run on iPhone 15, iPad Air, Pixel 8 and compare screenshots").
- **Universal device cloud / device mesh** (the "meant to be" vision): any agent on any machine claims any device — physical or virtual, iOS or Android, local or remote — with a Mac Studio serving connected iPhones/iPads to laptops over fed; pool understands device capability/battery/workload and routes intelligently; physical and cloud device farms abstracted behind one session interface. Genymotion cloud-emulator integration exists but is experimental.

## Technology stack
- **Language**: Python 3.11+ (supports 3.11–3.14), packaged via `pyproject.toml` (setuptools), version 0.3.0, MIT license, "Development Status :: 4 - Beta".
- **Core deps**: zero required runtime dependencies for the base CLI. Optional `[api]` extra pulls `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `zeroconf` (mDNS/service discovery). keel project.json lists `click` as the CLI framework.
- **Module layout** (`simemu/`): `cli.py`, `session.py`, `state.py`, `device.py`, `ios.py` (wraps `xcrun simctl`), `android.py` (wraps `adb`/`emulator`), `genymotion.py`, `discover.py`, `create.py`, `exclusive.py` (claim/lock logic), `lease.py` + `desktop_lease.py`, `claim_policy.py`, `device_aliases.py`, `monitor.py`, `watchdog.py`, `visibility.py`, `window.py`, `proof.py`, `schema.py`, `trace.py`, `fed.py`, `server.py`, `dashboard.py`, `ui/`.
- **Native companion**: Swift / SwiftUI menubar app under `simemu/swift/` (SimEmuBar, SwiftPM `Package.swift`).
- **Platform tooling wrapped**: Xcode/`xcrun simctl` (iOS), Android SDK (`adb`, `emulator`, `avdmanager`, uiautomator), Maestro (headless UI + a11y interaction), Genymotion (cloud emulators).
- **Persistence**: JSON session store at `~/.simemu/sessions.json` (fcntl.flock-locked, atomic writes, schema-versioned). Data/config/cache roots under `~/.simemu` (prod) and `~/.simemu-dev` (dev).
- **Lifecycle infra**: launchd monitor agent; auto-started localhost HTTP API on `127.0.0.1:8765`.
- **Quality tooling**: pytest suite under `tests/`; ruff (py311, line-length 120, broad lint select set).
- **Install**: `curl | bash` installer (`install.sh`) that checks Python, `pip install -e .`, sets up the launchd monitor, builds/installs SimEmuBar, installs the guard hook into `~/.claude/settings.json`, and repairs symlinks/binary paths; also `pipx`/PyPI distribution flow and a `Makefile` with `publish`/`publish-test` (twine) targets.
- **onlytools manifest**: tier T1, classification `core-tool`, status active, `serviceMode: cli-only`; fed identity `simemu`; proof receipts via proofy; sentinel manifest+graph checks required; runecode integration on.

## Roadmap & keel backlog (the ACTUAL backlog + DECISIONS; describe roadmap + big bets — do NOT judge done-ness/quality)
The keel backlog is organised into five named waves plus an "Unassigned" pool, all reported at 100% in the generated roadmap view; a final `launch-readiness` lane carries the only open (`todo`) tasks. Total ~90 task files (`T-001`–`T-037`, `T-LU-001`–`T-LU-053`).

- **Wave 1 — v2 Polish** (5/5): complete the v2 session API, build wrapper (`do build --variant`), cross-platform naming, permanent per-product device reservations, Scouty desktop-lease integration, pip publish + installer.
- **Wave 2 — Proof Reliability** (7/7): make proof capture and app-target validation deterministic across iOS, Android, and the menubar app — live visibility reconciliation from real window state, a `proof` mode that normalizes appearance/status-bar/render waits into an artifact bundle, iOS URL-handoff hardening, Android foreground-app verification (reject proof if the wrong app is foreground), per-command session provenance, menubar live refresh, server v2 parity for present/stabilize/verify-install/repair-install.
- **Wave 3 — Productization** (9/9): turn simemu into a stable installable tool — reservation pools per product+form-factor, claim-policy config (aliases/defaults), shell completions, pipx/PyPI reproducible release, installer hardening (PATH/wrapper/monitor/menubar), runecode/doctor integration, project integration kit, menubar device relabel/rename, persistent device aliases.
- **Wave 4 — Ops Hardening** (8/8): harden session state, diagnostics, APIs, recovery for long-running shared-desktop use — schema versioning/migrations, crash-safe atomic writes + corruption recovery, monitor/menubar watchdog, structured trace bundle export, real-device parity coverage, server auth + rate limiting for multi-user deployments, JSON-schema contracts for sessions/commands/server responses, real-iOS stable identifiers/discovery/screenshot path.
- **Wave 5 — Zero Tolerance** (12/12): every bug from a brutal assessment fixed plus security/quality gates — Android launch test timeouts, iOS boot-tolerance test, misplaced docstrings (dead code), missing subprocess timeouts in `android.py`, reboot/serial-invalidation, hardcoded AVD target, hardcoded `/tmp` path, operator-precedence and dead-statement cleanups, plus GATE tasks ("pytest exits 0", "zero un-timed subprocess.run", "all docstrings first statement").
- **launch-readiness lane (OPEN / `todo`)** — the live edge of the backlog ahead of the first public OSS release:
  - `T-LU-048` (high): maintainer home path + username hardcoded in shipping source (`simemu/device.py:300`).
  - `T-LU-049` (high): private/internal product names (goala, sitches, fitkind, univiirse, vivii, up2much, StrikeThePose, settle) leaking in source.
  - `T-LU-050` (medium): test hardcodes absolute paths to maintainer's private projects.
  - `T-LU-051` (medium): Makefile `publish`/`publish-test` twine-to-PyPI supply-chain item.
  - `T-LU-052` (high): add smoke E2E test (monitor starts + claim returns session).
  - `T-LU-053` (medium): tag and push v0.x — first public OSS release.

**Decisions**: the keel decisions view is empty (no formally logged DECISIONS records). Direction is captured in three docs instead: `KEEL_ROADMAP_PROPOSAL.md` (status snapshot + priority ordering), `docs/shared-desktop-plan.md`, and `PRODUCT_IT_WAS_MEANT_TO_BE.md`.

**Big bets**:
1. **Shared-desktop reliability** (`shared-desktop-plan.md`): the operating model where every product keeps permanent reserved "appliance" simulators, the human keeps using the Mac normally, and simemu acquires desktop focus only briefly then restores the user's previous app. New commands `present` and `stabilize`, a desktop busy-guard (pause/fail on recent keyboard/mouse activity), stabilized iOS tap-bounds with retries, and explicit `stable`/`unstable` diagnostics instead of silent misfires. Phased Phase 1→3; integrates with Scouty as the desktop/browser specialist via its lease API.
2. **Distributed device mesh** (`PRODUCT_IT_WAS_MEANT_TO_BE.md`): a universal device cloud over fed — local sims, connected physical devices, and cloud farms (Genymotion) abstracted behind one session/lease interface, with capability/battery/workload-aware routing and automatic cross-platform iOS+Android pairing and test matrices. The `/v2/leases` host/run-id/expiry API is the seam this is being built on.
3. **First public OSS release** (launch-readiness lane): privacy/secret scrub + smoke test + v0.x tag.

## Moat (defensibility)
- **Workflow lock-in / standard-of-record**: simemu is mandated for all sim/emulator ops across the vykeai estate — the guard hook actively *blocks* `xcrun simctl`/`adb`, so once installed it becomes the only sanctioned path to a device. Agents and projects encode `simemu do` into their AGENTS.md and execution configs, raising switching cost.
- **Narrow, real, under-served problem**: multi-agent device contention (agents booting/killing each other's simulators, screenshots capturing the wrong app) is a problem that essentially only exists once you run many AI coding agents in parallel — a niche generic mobile-CI tools (Fastlane, simctl wrappers, Appium, Maestro alone) don't address. The exclusivity/lock/PID-reaping machinery is the differentiated core.
- **Breadth of unified surface**: 50+ device commands behind one session interface spanning iOS + Android (+ nascent macOS), with consistent proof-envelope output — a substantial integration surface to replicate.
- **Ecosystem entanglement**: deep ties to the surrounding onlytools/vykeai stack — fed (discovery), Scouty (desktop lease/focus), keel (build/execution config), Atlas/Sentinel/Proofy (the `simemu.mobile-proof.v1` artifact envelope), runecode/doctor. Value compounds inside that ecosystem.
- **Operational know-how**: memory-budget parking, shared-desktop focus-restore, headless Maestro a11y interaction, and crash-safe session state encode hard-won reliability lessons rather than novel IP.
- **Moat limits**: MIT-licensed, pure-Python wrapping public Apple/Google tooling — no patent/data moat; defensibility is workflow adoption + ecosystem integration, not technology that can't be reimplemented.

## Target user & monetization (who pays + pricing/open-core model if known)
**Target user**: teams and individuals running **multiple AI coding agents in parallel on mobile apps** — the originating use case is vykeai's own fleet of Claude/Codex agents building iOS+Android features simultaneously. Generalises to AI-coding-agent platforms, mobile-focused agencies/studios running agent swarms, and CI/test-farm operators who need contention-free device allocation. Today it is local-machine and mac-centric (macOS required for iOS).

**Current commercial posture**: PUBLIC GitHub repo (`vykeai/simemu`), MIT, free; part of Luke's open-source onlytools dev-tooling initiative. No stated paid tier, no pricing, no hosted offering. Monetization is not defined in-repo.

**Plausible monetization paths (analysis of capabilities, not a verdict):**
- **Open-core**: keep the single-machine CLI + session manager free/MIT; gate the *distributed* layer — the device-mesh, cross-machine pool sharing, capability/battery-aware routing, and multi-device test-matrix orchestration — behind a paid tier. The `/v2/leases` host/run-id/expiry API and fed discovery are the natural open-core seam.
- **Hosted / cloud "device-as-a-service"**: operate the "universal device cloud" vision as a managed offering — a Mac Studio (or rack of mac minis + Android farm) serving exclusive iOS/Android sessions to remote agents over fed, billed per device-hour or per concurrent session. Genymotion-style cloud emulators slot in behind the same session interface. This is the "sell-the-shovels" play: AI builders and agencies running agent swarms pay for guaranteed, contention-free device capacity without managing their own mac/Android farm.
- **Sell-the-shovels to AI builders / agencies**: position simemu as the device-allocation layer that any multi-agent coding platform (or any agency running parallel agents) drops in. Monetize via a team tier covering the server auth + rate limiting (already built, Wave 4 `T-LU-012`), multi-user deployments, reservation-pool management, and the proof-envelope/Sentinel/Proofy integrations.
- **Licensing / support**: commercial license or support+SLA contracts for orgs that want the mesh + proof pipeline integrated into their own CI, given the MIT base.
- **Note**: a global memory `github-only-never-npm` indicates npm publishing is off estate-wide; here distribution is PyPI/pipx/`curl|bash`, with `publish`/`publish-test` twine targets flagged as a launch-readiness supply-chain item.

## Sources read
- `/Users/luke/dev/onlytools/simemu/README.md`
- `/Users/luke/dev/onlytools/simemu/PRODUCT_IT_WAS_MEANT_TO_BE.md`
- `/Users/luke/dev/onlytools/simemu/docs/NEXT_LEVEL.md`
- `/Users/luke/dev/onlytools/simemu/docs/shared-desktop-plan.md`
- `/Users/luke/dev/onlytools/simemu/KEEL_ROADMAP_PROPOSAL.md`
- `/Users/luke/dev/onlytools/simemu/views/roadmap.md`, `views/tasks.md`, `views/decisions.md`
- `/Users/luke/dev/onlytools/simemu/keel/project.json` and `keel/tasks/*.json` (T-001..T-037, T-LU-001..T-LU-053)
- `/Users/luke/dev/onlytools/simemu/pyproject.toml`, `onlytools.manifest.json`, module listing under `simemu/`, Swift companion under `simemu/swift/`
- `git log` and `gh repo view vykeai/simemu` (PUBLIC, Python, MIT in pyproject, created 2026-03-05, last push 2026-06-18, 0 stars)
- Not consulted: Mac Studio copy (local repo was complete and current); no separate DECISIONS.md exists (keel decisions view empty).
