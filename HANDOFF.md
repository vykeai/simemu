# HANDOFF — Session 7 (2026-05-03)

## Completed this session

### OnlyMenuBarKit v2: OnlyBarController adopted across all menu bar apps

All 4 NSPopover-based menu bar apps now use `OnlyBarController` from `onlystack/libs/desktop-tooling-core/swift/OnlyMenuBarKit`.

| App | Commit | Net diff | Notes |
|---|---|---|---|
| **SimEmuBar** | (prior session) | +183/-395 | First adopter, fixed infinite recursion bug |
| **Llodge** | `aa951f11` | +13/-42 | Already had kit UI, just controller swap |
| **Sweech** | `2de14c2` | +112/-25 | MenuBarExtra → NSPopover, emoji text as NSImage |
| **Mounty** | `9ba92cb` | +124/-94 | Custom PNG, dynamic height, right-click menu |

### OnlyBarController enhancements (onlystack `11f5100`)

- Configurable `statusItemLength` param (Mounty: `.variableLength`)
- Right-click context menu via `handleClick(_:)` + `sendAction(on: [.leftMouseUp, .rightMouseUp])`
- Public `statusBarButton` accessor (for onboarding popovers)
- `closePopover()` for programmatic dismiss
- `NSApp.activate(ignoringOtherApps: true)` on popover show

### Bug fix: SimEmuBar infinite recursion

`renderIcon()` set `badgeCount` → `didSet` → `updateIcon()` → `renderIcon()` → stack overflow (exit 139). Fixed by removing state mutations from `renderIcon()`. `syncIcon()` is sole mutation point.

### Sweech known issue

Minor rendering nits inside popover after MenuBarExtra → NSPopover migration. Cosmetic.

### Apps NOT migrated (lower priority)

- **Macmory** — dual popover + floating shelf panel, complex
- **Loopy** — MenuBarExtra(.window), minimal
- **Knowy** — MenuBarExtra(.window), cross-platform iOS/macOS
- **Scouty** — Python rumps, not Swift
- **Embee** — not a menu bar app (Ember mug iOS/macOS)

## All changes pushed to origin/main in all repos.

## Next up (from keel backlog)

1. **T-LU-037**: Audit all repos for hardcoded ports → fed discovery
2. **T-LU-047**: Expand sentinel to lock bundle IDs
3. **T-LU-039**: Remove hardcoded paths from sweech
4. **T-LU-038**: Fed version notifications
