---
id: DANDER-208
title: Add a stable qualification entrypoint
status: in-review
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200]
created: 2026-08-16
---

## Context

Two exact-RC27 Kubernetes infrastructure preflights failed before Python started because their
manifests assumed different interpreter paths in the local and source-free immutable images. Future
qualification Jobs need one image-owned command that does not expose installation layout.

## Acceptance Criteria

- [x] `dander qualification-run SCRIPT [ARG ...]` uses the installed runtime interpreter and
  forwards harness arguments without an image-internal Python path.
- [x] Missing or unreadable harnesses fail clearly before execution without logging file contents.
- [x] Protected container CI invokes an operator-mounted probe through the image's normal rootless,
  read-only entrypoint.
- [x] Phase 8 guidance requires the stable command for future manifests without invalidating or
  rerunning accepted RC27 evidence.
- [x] Focused tests, Ruff, canonical strict typing, and the local container contract pass.
- [ ] Protected CI and PR review pass before merge.

## Design

Register one narrow root command that validates a trusted operator-mounted Python file, then
replaces the CLI process with that script through `sys.executable`. Keep qualification orchestration,
objectives, provider logic, and script contents outside the command.

## Implementation Notes

- The root command checks only that the trusted harness is a readable file, then uses `os.execv`
  with `sys.executable`; arguments, signals, environment, and terminal status remain process-native.
- The protected container job mounts a credential-free probe and invokes it through the default
  non-root entrypoint under a read-only filesystem, without any interpreter path.
- Local verification passed 1,742 tests, Ruff on 451 files, canonical strict typing on 419 files,
  Control-contract drift, the installed command probe, and the built-image smoke.

## Review Log

_Protected CI and PR review are the completion gate._
