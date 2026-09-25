# Agent instructions (Codex and others)

Read CLAUDE.md first — it is the authoritative project context and its
constraints (2 GB / 1 vCPU Render deploy, memory architecture, watchdog
rules, audio signal-chain rules, known-deliberate deletions) are binding.

When REVIEWING: report findings ranked by severity with file:line and a
concrete failure scenario for each; verify claims by reading code and
running what you can (scripts/smoke.py if present; scratch venvs are fine);
make no code changes unless explicitly asked to fix findings.

Verification floor: `python -m py_compile` proves nothing works — run the
real path. There is no test suite beyond scripts/smoke.py (if present).

## Project state and update duty

CLAUDE.md's "Project status" section is the cross-session memory for this
repo. Read it before starting any task. Every task that changes code or
project state MUST update that section as its final step — move shipped
items, adjust In flight, append new debts to Backlog. A task that leaves
the status section stale is not finished.

## Review conduct

- The branch under review is FROZEN: no commits land on it while the
  review runs. If a commit appears mid-review, say so and stop.
- Every measured claim needs its artifact retained in the repo (CLAUDE.md
  working agreements). A number whose artifact is gitignored or
  overwritten is an unsupported claim — flag it.
- Report platform (macOS vs Linux, cold vs warm) with every measurement.
