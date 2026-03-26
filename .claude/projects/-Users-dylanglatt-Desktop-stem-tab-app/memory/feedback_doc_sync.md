---
name: Mandatory documentation sync after every change
description: Every code change must be followed by updating all project docs (README, TODO, CHANGELOG, PROJECT_CONTEXT) before reporting completion
type: feedback
---

Every code change requires updating project documentation before the task is considered complete.

**Why:** Documentation fell significantly behind the codebase — entire modules (harmonic_analysis.py), the full UI rebuild (9 templates), database migration, redesign, and auth system were all undocumented. User wants docs to always reflect reality.

**How to apply:** After any code change: (1) update design/CHANGELOG.md with the change, (2) update design/TODO.md to mark completed items or add new ones, (3) update design/PROJECT_CONTEXT.md if architecture/features/known-issues changed, (4) update README.md if user-facing behavior changed. Also read .claude/RULES.md at the start of every task.
