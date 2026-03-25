# simemu Integration Kit

Copy these files into your project to integrate with simemu:

## Files

| File | Destination | Purpose |
|------|-------------|---------|
| `AGENTS-simemu-snippet.md` | Append to your `AGENTS.md` | Agent instructions for using simemu |
| `execution.yaml` | `keel/execution.yaml` | Build variant config for `simemu do $SESSION build` |

## Setup

1. Copy `AGENTS-simemu-snippet.md` contents into your project's `AGENTS.md`
2. Copy `execution.yaml` to `keel/execution.yaml` and customize the scheme/task names
3. Run `simemu doctor` to verify the setup
4. Agents can now use `simemu claim` / `simemu do` in your project

## Verify

```bash
# Check simemu is healthy
simemu doctor

# Test a claim
SESSION=$(simemu claim ios | jq -r .session)
simemu do $SESSION screenshot -o /tmp/test.png
simemu do $SESSION done
```
