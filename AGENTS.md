<!-- version: f823fe5a2488 | generated: 2026-03-27 -->
# simemu



---

## Screenshot Storage

Save every screenshot to `~/Desktop/screenshots/simemu/`.

```bash
export PROJECT_SCREENSHOT_DIR=~/Desktop/screenshots/$(basename "$(git rev-parse --show-toplevel)")
mkdir -p "$PROJECT_SCREENSHOT_DIR"
```

Do not keep proof or review screenshots in `/tmp`.
**Prove deliverables with screenshots — never verbal claims.**
For every change, gather fresh proof and explicitly analyse the result before
calling the work done. "It looks correct" is not proof. A screenshot IS proof.
When the change affects UI, capture a fresh screenshot from the actual
simulator/device and review it visually before marking the task done.

---

## Project Management — Keel

This project is managed by **keel** ([vykeai/keel](https://github.com/vykeai/keel)). Keel is the single source of truth
for tasks, specs, decisions, and roadmap. **Do NOT create or maintain manual TASKS.md,
ROADMAP.md, or similar tracking files.**

### With MCP access (preferred)
- Read state: `keel_status`, `keel_list_tasks`
- Start work: `keel_update_task { id, status: "active", assignee: "claude" }`
- Finish: `keel_update_task { status: "done" }` + `keel_add_note` with summary
- Blocked: `keel_update_task { status: "blocked" }` + `keel_add_note` with reason
- Architecture changes: `keel_update_architecture_doc`
- Decisions: `keel_log_decision` before implementing
- Search first: `keel_search "topic"` — update existing, don't duplicate

### Without MCP
- CLI: `keel status`, `keel tasks`, `keel task update <id> --status active`
- Read `views/` for current state — never edit views (generated)

---

## Autonomous Execution

This project uses **AUTONOMOUS_EXECUTION.md** for continuous execution rules.
If the file exists, read it — it contains the full execution mandate, quality
gates (13 gates), task loop, and conventions.

If it doesn't exist yet, run:
```bash
keel render:install-profiles   # install shared templates
keel render:auto               # generate AUTONOMOUS_EXECUTION.md
```

**Key files for execution:**
- `AUTONOMOUS_EXECUTION.md` — the execution playbook (generated, do not edit)
- `ONE_OFF.md` — ad-hoc tasks (human adds, agent executes + cleans up)
- `keel/execution.yaml` — build commands, variants, platforms

## Required Reading Order

When landing in this repo, read in order before starting work:

1. `AGENTS.md` (this file) — project identity, conventions
2. `AUTONOMOUS_EXECUTION.md` — how to execute without stopping
3. `views/roadmap.md` — current wave state
4. `views/tasks.md` — all tasks with dependencies

Interpretation:
- `AGENTS.md` = what the project is and how to work in it
- `AUTONOMOUS_EXECUTION.md` = the execution loop, quality gates, all rules
- `views/` = generated current state, never edit directly

---

## Skills — Runecode

**runecode** ([vykeai/runecode](https://github.com/vykeai/runecode)) provides reusable Claude Code skills:

| Skill | Purpose |
|-------|---------|
| `/test-write` | Write tests for changed code |
| `/review-self` | Review your own code before committing |
| `/security-audit` | Audit changes for vulnerabilities |
| `/dead-code` | Find unused exports and unreachable code |
| `/tech-debt` | Identify technical debt |
| `/pr-description` | Write PR description from current diff |

**Project health**: `runecode doctor` checks setup. `runecode audit` scores and auto-fixes gaps.

---

## Python Conventions

- **Python 3.11+** — use modern syntax (match/case, type unions with `|`)
- **Type hints everywhere** — all function signatures, return types, class attributes
- **Pydantic v2** for data validation where applicable
- **async/await** for I/O-bound code — never block the event loop
- **No bare `except:`** — always catch specific exceptions
- **f-strings** for formatting — never `.format()` or `%`
- **pathlib.Path** over `os.path` for file operations
- **`if __name__ == "__main__":`** guard on all executable modules

---

## Key Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # or: pip install -r requirements.txt
pytest                      # run tests
ruff check .                # lint
ruff format .               # format
```

---

## Testing (pytest)

- Test files: `tests/test_*.py`
- Use pytest fixtures for setup/teardown
- Mock external services — never hit real APIs in tests

---

## Git Conventions

- Commit after every meaningful chunk of work — do not accumulate changes
- Concise messages in imperative mood: `feat:`, `fix:`, `refactor:`, `docs:`
- Never commit `.env`, credentials, or secrets
- Progressive commits: after each file in multi-file tasks, not all at the end

---

## Critical Agent Safety Rules

### NEVER delete prototype UI to "make honest"
When a task says "make honest", "make real", or "align to launch scope", the correct approach is:
- Wire real APIs behind the existing UI
- Keep stub/mock data as offline fallback
- Replace hardcoded data with API calls that fall back to mocks on failure

The WRONG approach (which has caused significant code loss) is:
- Deleting rich UI and replacing with empty placeholders
- Stripping navigation items because they use mock data
- Removing features because they aren't "real" yet
- Interpreting "launch scope" as "delete everything except the minimum"

**If a screen has mock data, the mock data IS the design spec.** The agent's job is to make it real, not delete it.

### NEVER strip navigation items during scope narrowing
When narrowing scope:
- Comment out or feature-flag — never delete
- All routes, tabs, and menu items must remain in code (can be gated behind feature flags)
- If a feature is "not in launch scope", hide it behind a flag, don't remove it

### Evidence of past failures
- FitKind: 25 "make honest" commits deleted 3,314 lines of prototype UI across 13 files
- Univiirse: "launch scope" commits stripped Social tab, Worlds, Codex, Leaderboard, and Library smart sections
- Both cases required restoration from worktrees or git history

---

## Do Not

- Blame "pre-existing" issues — if the build is broken, fix it. If tests fail, fix them. Your end state must be building, tested software.
- Use `os.path` when `pathlib.Path` works
- Use bare `except:`
- Use mutable default arguments (`def f(x=[])`)
- Use `print()` for logging — use the `logging` module
- Use `subprocess.run(shell=True)` without sanitizing input

---

## Definition of Done

- [ ] All tests pass (`pytest`)
- [ ] Type checks pass (`mypy` or `pyright`) if configured
- [ ] No lint errors (`ruff check .`)
- [ ] `/review-self` passed
- [ ] Changes committed (frequent, progressive)
- [ ] Keel task updated: `keel_update_task { status: "done" }` + `keel_add_note`

---

## Git Branch & Worktree Lifecycle

This section is mandatory for any agent that creates branches or worktrees.

### 1. Discover the gitflow before branching

Before creating any branch or worktree, determine the repo's integration model:

```bash
git remote show origin | grep "HEAD branch"   # default branch (usually main)
git branch -r | grep develop                  # does a develop branch exist?
```

- If `develop` exists → feature branches target `develop`, not `main`
- If only `main` exists → feature branches target `main`
- Never guess. Always check.

### 2. Worktree placement

**Never** place a worktree in `/tmp` or `/private/tmp`. Those paths are wiped on reboot, leaving orphaned `.git/worktrees/` metadata with no working directory.

Use one of these instead:
- `<repo-root>/.worktrees/<branch-name>/` — co-located, automatically gitignored
- `<repo-parent>/<repo-name>-<task-id>/` — sibling directory

### 3. Complete the full merge cycle before declaring done

A task is **not done** when code is written. It is done when:

1. All commits are on the feature branch
2. Feature branch is merged (via PR or `git merge`) into the target integration branch (`develop` or `main`)
3. Feature branch is deleted: `git branch -d <branch> && git push origin --delete <branch>`
4. Worktree is removed: `git worktree remove <path>`
5. Keel task is updated to `done`

Never leave a feature branch open after work is merged.

### 4. Abandoned or blocked tasks

If a task is blocked or abandoned mid-flight:
- Keep the branch on the remote (don't delete — work may resume)
- **Remove the local worktree**: `git worktree remove --force <path>`
- Update Keel status to `blocked` with a note linking the branch name

### 5. Audit before starting new work

Before opening a new worktree, run:

```bash
git worktree list
```

If you see more than 2–3 open worktrees, stop and clean up stale ones first. Accumulating worktrees causes merge conflicts, confusion, and disk waste.

### 6. Temporary/dirt branches

`temp-dirt-*` branches created as safety snapshots must be deleted within the same session once the real branch is verified. Never push `temp-dirt-*` branches to the remote as a permanent artifact.
