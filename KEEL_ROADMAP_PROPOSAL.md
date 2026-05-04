# Simemu — Keel Roadmap Proposal

## Status
- **Tasks:** 37 total (30 done, 1 active, 6 new)
- **Done:** 30
- **Active:** T-011 (remove dead legacy command handler code)
- **Wave:** v2 Polish

## What was added (T-032 to T-037)

| ID | Title | Priority | Source |
|----|-------|----------|--------|
| T-032 | Menubar app macOS 26 compatibility | high | Known bug |
| T-033 | Desktop lease coordination — simemu present | high | shared-desktop-plan.md |
| T-034 | Shared-desktop reliability — brief focus, user app restore | high | shared-desktop-plan.md |
| T-035 | Permanent device reservations per product | medium | shared-desktop-plan.md |
| T-036 | Integrate Scouty desktop lease API for multi-tool focus management | medium | Cross-tool dependency |
| T-037 | pip publish simemu + install.sh improvements | medium | Distribution |

## Cross-reference with deep analysis

The shared-desktop-plan.md describes a clear reliability gap when humans and agents share the same Mac. Three tasks (T-033, T-034, T-035) directly address this. Scouty already exists as the desktop/browser specialist, so T-036 is an integration and contract-hardening task rather than a greenfield tool build.

The NEXT_LEVEL.md confirms all v2 commands are implemented. The remaining work is polish and reliability, not new commands.

## Gaps
- T-001 (Build wrapper) is still todo — this was the original wave goal and hasn't been started
- No test coverage task for the 6 new features
- No documentation task for the new desktop lease/present commands
- Scouty dependency (T-036) now depends on scouty lease API stabilization and hardening, not a greenfield scouty build

## Recommended priority order
1. T-001 (Build wrapper) — original wave objective
2. T-032 (macOS 26 menubar) — active bug
3. T-033 + T-034 (Desktop lease + reliability) — biggest reliability improvement
4. T-011 (Legacy cleanup) — already active
5. T-035 + T-036 + T-037 (Reservations, scouty lease API, publish) — lower priority
