# simemu -- Product Archaeology

## Vision

simemu is session-based simulator and emulator allocation for multi-agent mobile development.
When multiple AI agents build features simultaneously, they fight over simulators -- one boots
a device, another kills it, screenshots capture the wrong app. simemu solves this with
session-based allocation: each agent claims a device, all operations go through `simemu do`,
and direct xcrun/adb calls are forbidden.

## What It Does

- **Session-based allocation**: `simemu claim ios` or `simemu claim android` returns a session
  ID for exclusive device access
- **Unified device operations**: `simemu do $SESSION install`, `launch`, `screenshot`, `tap`,
  `maestro`, `url` -- one command interface for all device interactions
- **Multi-agent safety**: sessions are exclusive; no two agents can claim the same device
- **Form factor selection**: phone (default), tablet, watch, tv, vision via `--form-factor`
- **OS version pinning**: `--version 26` for specific OS versions
- **Real device preference**: `--real` flag to prefer physical devices over simulators
- **Automatic lifecycle management**: boot, shutdown, recovery all handled internally -- agents
  never manage device state
- **Session expiry and re-claim**: sessions expire after inactivity; error messages include the
  exact re-claim command to run
- **Maestro test integration**: `simemu do $SESSION maestro flow.yaml` for automated UI testing
- **Deep link testing**: `simemu do $SESSION url "myapp://deep/link"`
- **Dashboard**: web UI showing active sessions, device state, and utilization
- **Fed integration**: registers with fed for service discovery across the network
- **Guard hooks**: shell wrappers and pre-commit hooks to prevent direct xcrun simctl / adb usage

## Architecture

- **Language**: Python
- **Package structure** (under `simemu/`):
  - `cli.py` -- command-line interface (claim, do, list, release)
  - `session.py` -- session allocation, tracking, and lifecycle state machine
  - `ios.py` -- iOS simulator management (wraps xcrun simctl internally)
  - `android.py` -- Android emulator management (wraps adb/emulator)
  - `genymotion.py` -- Genymotion cloud emulator integration
  - `device.py` -- unified device abstraction layer
  - `state.py` -- session state machine (claimed, active, expired)
  - `monitor.py` -- health monitoring and utilization tracking
  - `server.py` -- HTTP server for dashboard and remote session management
  - `dashboard.py` -- web-based session and device overview
  - `discover.py` -- device discovery across platforms
  - `create.py` -- on-demand device creation
  - `window.py` -- simulator window management
  - `fed.py` -- fed service registration and discovery
  - `swift/` -- optional native macOS menu bar companion
  - `ui/` -- terminal UI components
- **Testing**: pytest test suite under `tests/`
- **Distribution**: Python package via pyproject.toml

## Current State

**What works:**
- iOS simulator support is mature and production-used across all vykeai mobile projects
  (sitches, goala, StrikeThePose, settle)
- Android emulator support is functional
- Session lifecycle (claim, use, expire, re-claim) is stable and reliable
- Multi-agent concurrent usage is tested -- no device conflicts
- Form factor and OS version selection work
- Maestro test flow integration works
- Dashboard shows active sessions and device state
- Fed integration is operational
- Guard hooks prevent direct xcrun/adb calls in all vykeai projects

**Known limitations:**
- Genymotion cloud integration is experimental
- Real device support (`--real` flag) is basic compared to simulator management
- No cross-machine device pool sharing yet (devices are local to each machine)
- watchOS and visionOS support is early

## What It Was Meant To Be

The unrealized vision is a universal device cloud where any agent on any machine can claim any
device -- physical or virtual, iOS or Android, local or remote. A Mac Studio with four connected
iPhones and two iPads would serve devices to agents running on laptops across the network
through fed. The pool would understand device capabilities, battery state, and current workload,
and intelligently route claims to the best available device.

Agents working on cross-platform features would automatically get paired iOS and Android
devices. simemu would manage not just individual device sessions but entire test matrices --
"run this on iPhone 15, iPad Air, and Pixel 8 simultaneously and compare screenshots." Device
farms (physical and cloud-based) would be abstracted behind the same session interface -- a
claim for an iPhone 16 Pro would transparently resolve to a local simulator, a connected
physical device, or a cloud-hosted instance depending on availability and capability needs.

Today simemu solves the critical problem of multi-agent device conflicts on a single machine.
The vision of a distributed, intelligent device mesh -- where device access is as simple and
location-independent as model routing through omnai -- remains the work ahead.
