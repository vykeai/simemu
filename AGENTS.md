# simemu

Simulator and emulator allocation manager for multi-agent iOS/Android development.

---

## Screenshot Storage

Save every screenshot to `~/Desktop/screenshots/simemu/`.

```bash
export PROJECT_SCREENSHOT_DIR=~/Desktop/screenshots/$(basename "$(git rev-parse --show-toplevel)")
mkdir -p "$PROJECT_SCREENSHOT_DIR"
```

Do not keep proof or review screenshots in `/tmp`.

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

## Execution — Cloudy

Use **cloudy** ([vykeai/cloudy](https://github.com/vykeai/cloudy)) for multi-task orchestration.

```bash
cloudy plan --spec ./docs/spec.md          # decompose spec → task graph
cloudy run --execution-model sonnet        # execute all tasks
cloudy check                               # re-validate completed tasks
```

**From inside Claude Code sessions** — unset nesting vars:
```bash
env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT cloudy run --spec spec.md
```

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

- **Python 3.11+** — use modern syntax (match/case, type unions with `|`, etc.)
- **Type hints everywhere** — all function signatures, return types, and class attributes
- **Pydantic v2** for data validation and settings where applicable
- **async/await** for I/O-bound code — never block the event loop
- **No bare `except:`** — always catch specific exceptions
- **f-strings** for string formatting — never `.format()` or `%` interpolation
- **pathlib.Path** over `os.path` for file operations
- **`if __name__ == "__main__":`** guard on all executable modules

---

## Project Structure

```
simemu/
├── simemu/     # or src/ — main package
│   ├── __init__.py
│   └── ...
├── tests/
│   ├── test_*.py       # or simemu/tests/
│   └── conftest.py
├── pyproject.toml       # package metadata + dependencies
└── README.md
```

---

## Key Commands

```bash
# Virtual environment
python3 -m venv .venv && source .venv/bin/activate

# Install
pip install -e ".[dev]"     # or: pip install -r requirements.txt

# Test
pytest                      # or: python -m pytest
pytest -x                   # stop on first failure
pytest -k "test_name"       # run specific test

# Type check
mypy .                      # or: pyright

# Lint
ruff check .
ruff format .
```

---

## Testing (pytest)

- Test files: `tests/test_*.py` or `tests/**/test_*.py`
- Use `pytest` fixtures for setup/teardown
- Use `pytest.mark.asyncio` for async tests
- Mock external services — never hit real APIs in tests
- Aim for meaningful coverage, not 100% line coverage

---

## FastAPI Patterns (if applicable)

- All routes prefixed `/api/v1`
- CRUD: GET (list/get), POST (create), PATCH (update), DELETE
- Use Pydantic models for request/response schemas
- Async locking for concurrent file/DB writes
- Health check endpoint: `GET /health`

---

## Git Conventions

- Commit after every meaningful chunk of work
- Concise messages: `feat:`, `fix:`, `refactor:`
- Never commit `.env`, credentials, `__pycache__/`, or `.venv/`

---

## Do Not

- Use `os.path` when `pathlib.Path` works
- Use bare `except:` — always catch specific exceptions
- Use mutable default arguments (`def f(x=[])`)
- Import from `__pycache__` or `.pyc` files
- Use `print()` for logging — use the `logging` module or structured output
- Leave debug `print()` statements in shipped code
- Use `subprocess.run(shell=True)` without sanitizing input

---

## Definition of Done

- [ ] All tests pass (`pytest`)
- [ ] Type checks pass (`mypy` or `pyright`) if configured
- [ ] No lint errors (`ruff check .`)
- [ ] `/review-self` passed — no obvious issues in diff
- [ ] Changes committed (frequent, progressive — not batched at end)
- [ ] Keel task updated: `keel_update_task { status: "done" }` + `keel_add_note`
- [ ] If using cloudy: all three validation phases pass

---

## Worktree Discipline (CRITICAL — applies to ALL skills and agents)

Work is either **sequential** or **parallel**. Never orphan a worktree.

**Sequential work (one task at a time):**
- Stay on the active branch. Do NOT create a worktree.
- Commit and push directly on that branch.

**Parallel work (multiple tasks in flight — e.g. `/vy-go`, `/loop`, `/looperator-start`, multiple `Agent` calls):**
- Each parallel task MUST run in its own `git worktree add .worktrees/<task-id> HEAD`.
- Before the orchestrator exits, every worktree MUST be either:
  1. **Merged back** into the active branch (`git merge --no-ff .worktrees/<id>`), gates re-run on the merged result, then `git worktree remove .worktrees/<id>`, OR
  2. **Explicitly surfaced** to the user as "needs manual merge" with the branch name preserved — never silently left behind.
- If a merge fails: keep the worktree, mark the task blocked, tell the user. Do NOT delete unmerged work.
- Before finishing any session that spawned parallel agents, run `git worktree list` and account for every entry.

**Why:** prior `/vy-go` runs lost work because independent worktrees were abandoned when the orchestrator exited without merging. This rule is non-negotiable.

---

## Docker Container Naming

When creating Docker containers (docker-compose, Dockerfile, scripts), **always prefix container names with the project name** so they are identifiable in Docker Desktop and `docker ps`.

**Format:** `<project-name>-<variant>-local`

Examples:
- `univiirse-api-local`, `univiirse-db-local`, `univiirse-redis-local`
- `fitkind-api-local`, `fitkind-worker-local`
- `den-web-local`, `den-postgres-local`

In `docker-compose.yml`, set `container_name:` explicitly on every service:
```yaml
services:
  api:
    container_name: myproject-api-local
  db:
    container_name: myproject-db-local
```

**Why:** Generic names like `api`, `infra`, `tmp`, `e2e` are unidentifiable when multiple projects run simultaneously. The `-local` suffix distinguishes dev containers from production.

---

## No CI/CD Pipelines

**Do NOT create GitHub Actions workflows, CI configs, or automated pipeline files.**

This means:
- No `.github/workflows/*.yml`
- No `.gitlab-ci.yml`, `Jenkinsfile`, `Taskfile`, `Makefile` for CI
- No `circle.yml`, `bitbucket-pipelines.yml`, `azure-pipelines.yml`
- No pre-commit hooks that phone home or run cloud services

**Why:** CI/CD will be set up intentionally when the project approaches production. Agents creating workflows during early development generates thousands of notification emails and adds maintenance burden before it provides value. Build, test, and lint locally.

If the project already has CI files, leave them alone -- but do not create new ones or extend existing ones without explicit instruction.


---

## Boil the Ocean

The marginal cost of completeness is near zero with AI. Do the whole thing. Do it right. Do it with tests. Do it with documentation. Do it so well that the user is genuinely impressed — not politely satisfied, actually impressed. Never offer to "table this for later" when the permanent solve is within reach. Never leave a dangling thread when tying it off takes five more minutes. Never present a workaround when the real fix exists. The standard isn't "good enough" — it's "holy shit, that's done." Search before building. Test before shipping. Ship the complete thing. When asked for something, the answer is the finished product, not a plan to build it. Time is not an excuse. Fatigue is not an excuse. Complexity is not an excuse. Boil the ocean.
