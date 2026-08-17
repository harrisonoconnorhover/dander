---
id: DANDER-224
title: Isolate the canonical local type-check environment
status: done
component: tooling
epic: cloud-portability-phase-8
created: 2026-08-17
---

## Context

Running a focused Snowflake test first installed the optional Snowflake SDK into the worktree
environment. The canonical checker then reused that environment and reported an `unused-ignore`
failure that the fresh protected-CI environment does not produce.

## Acceptance Criteria

- [x] Make the canonical checker independent of packages installed by earlier focused checks.
- [x] Keep the locked dev/PostgreSQL extras and the repository-configured mypy target list.
- [x] Document the environment isolation without changing the protected command.
- [x] Reproduce the enriched environment and verify the canonical command passes.

## Design

Use `uv run --isolated` inside `scripts/check_types.py`. The public command remains
`python3 scripts/check_types.py`; `uv` creates and removes the exact temporary environment.
