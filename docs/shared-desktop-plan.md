# Shared-Desktop Simulator Reliability Plan

## Problem

`simemu` already solves simulator ownership. Each product or agent can reserve a
dedicated slug and avoid clobbering another project's install or boot state.

That does **not** fully solve interactive reliability on a human-used Mac.

The remaining failure mode is shared-desktop interference:

- the human is actively using the computer, even if they are not using the simulators
- Simulator / Emulator windows move or lose focus
- iOS taps depend on accessibility-derived content bounds
- coordinate gestures run while the desktop is in a transient state

This creates fragile runs where:

- taps fail because window bounds cannot be resolved
- taps land on stale coordinates
- Simulator steals focus from the human's current app
- retries keep acting blindly instead of failing with a useful reason

## Operating Model

The correct model for a capable shared Mac is:

1. Every product keeps permanent reserved simulators.
2. Those simulators behave like dedicated appliances.
3. The human keeps using the Mac normally.
4. `simemu` acquires desktop control only briefly for focus-dependent actions.
5. `simemu` restores the user's previous app immediately after interaction.

## Workstreams

### 1. Reservation by product or agent

Keep permanent reserved slugs per project:

- `sitches-ios`, `sitches-android`
- `fitkind-ios`, `fitkind-android`
- etc.

This prevents cross-product device pollution.

### 2. Presentation state

Each reserved simulator should have a canonical presentation state:

- visible window
- known orientation
- known scale
- known position
- stable content bounds

New command:

- `simemu present <slug>`

Purpose:

- restore the simulator window into a known visible state before screenshots or taps

### 3. Stabilization preflight

Before focus-dependent gestures, `simemu` should verify the desktop is ready.

New command:

- `simemu stabilize <slug>`

Checks:

- simulator is booted
- target window is visible
- content bounds can be resolved
- desktop is idle enough for interaction
- focus can be acquired if needed

Result:

- explicit `stable` / `unstable`
- useful diagnostics instead of silent misfires

### 4. Focus restore

Interactive operations should not leave Simulator frontmost.

For focus-dependent iOS interactions:

1. capture the current frontmost app
2. raise Simulator briefly
3. perform the gesture
4. restore the previous app

This is mandatory for a shared-computer workflow.

### 5. Desktop busy guard

The human may be using the Mac even when they are not using simulators.

`simemu` should pause or fail cleanly when:

- recent keyboard activity exists
- recent mouse activity exists
- desktop focus is changing rapidly

Interactive commands should wait briefly for idle instead of fighting the user.

### 6. Safer iOS tap flow

Current iOS tap failure modes:

- content bounds are resolved ad hoc each time
- retries are blind
- focus is stolen with no restore

Improve by:

- resolving bounds through stabilization
- retrying bounds acquisition before gesture posting
- failing early when the desktop is unstable
- restoring the human's previous frontmost app afterwards

### 7. System alerts

Common blockers:

- notification permission prompts
- photo/location/camera prompts
- app-open confirmation prompts

`simemu` should detect known alerts where possible and surface them as explicit blockers.

### 8. Reduce UI-driving dependence

The app layer should still help:

- one-hop debug auth routes
- one-hop authenticated tab routes
- correct local API defaults per platform

The fewer manual taps required, the more reliable the overall workflow becomes.

## Implementation Order

### Phase 1

- add `present`
- add `stabilize`
- add frontmost-app restore around iOS tap
- add desktop idle guard for focus-dependent iOS interactions
- make iOS tap use stabilized bounds with retries

### Phase 2

- extend safer behavior to iOS swipe and long-press
- persist canonical presentation state per slug
- improve iOS alert detection

### Phase 3

- stronger desktop-aware scheduling
- optional multi-space or dedicated-display presentation rules
- richer diagnostics in daemon / API responses

## Definition of Done

The shared-desktop model is working when:

- every product has dedicated simulators
- the human can keep using the Mac normally
- interactive iOS commands stop misfiring silently
- Simulator no longer stays frontmost after agent gestures
- stabilization failures are explicit and actionable
- product flows need fewer interactive steps because app debug entry is stronger
