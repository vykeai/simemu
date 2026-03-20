# simemu — Product Archaeology

## What It Was Meant To Be

**Session-based simulator and emulator allocation for multi-agent development.**

When multiple AI agents build features simultaneously, they fight over simulators — one boots a device, another kills it, screenshots capture the wrong app. simemu solves this with session-based allocation: each agent claims a session, all operations go through `simemu do`, and direct xcrun/adb calls are blocked.

## Core Identity

- Session-based allocation: `simemu claim` gives you a device, `simemu do` uses it
- Multi-agent safe — no device conflicts between concurrent agents
- Abstracts both iOS simulators and Android emulators behind one interface
- Automatic lifecycle management — boot, shutdown, recovery handled internally
- Guard hooks prevent direct xcrun simctl / adb calls

## The Gap It Fills

Mobile development with multiple agents is impossible without device coordination. simemu turns simulator management from a source of constant breakage into a reliable utility that agents can use without thinking about device state.

## Status

Active development. iOS simulator support mature. Android emulator support functional. Session expiry and re-claim flow working.
