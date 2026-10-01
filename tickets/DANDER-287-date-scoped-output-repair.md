---
id: DANDER-287
title: Repair selected output dates from retained raw data
status: in-code
component: python
depends_on: [DANDER-286]
created: 2026-10-01
---

## Context

A full replay is too broad when an operator wants to rebuild a known date interval. Add that
bounded operation to the existing Control lifecycle and direct BigQuery graph runtime.

## Acceptance Criteria

- [x] Preview the exact saved graph, UTC interval, eligible routes, and publication limits.
- [x] Retain the window in durable identity, history, replay, and Cloud Run execution settings.
- [x] Skip source extraction and watermark changes; require the exact graph and lease fence.
- [x] Preserve surrounding rows, reject crossing keys, and publish atomically per output.
- [x] Reject incompatible backends and older runtimes before pipeline work.
- [x] Reconcile terminal Job cleanup without overwriting a newer deployment or launch.
- [x] Explain measured publication counts separately from the zero ingestion counts.
- [ ] Verify a bounded synthetic BigQuery and Cloud Run run from an immutable current artifact.
- [ ] Publish the paired contract artifact and integrate the guided Druff controls.

## Verification and boundary

Focused runtime, API, lifecycle, Cloud Run, explanation, and transaction tests passed. The full
local suite passed 2,429 tests and skipped 56; its 28 loopback-server failures all passed when
rerun with socket permission. The additional explanation test passed in its 17-test file.
Canonical strict typing passed across 516 files; Ruff and generated contract checks passed.

[PR #548](https://github.com/harrisonoconnorhover/dander/pull/548) merged as `79aa1cb` after all
protected checks passed, including **2,515 tests** with PostgreSQL, distribution installation,
container and secret scans, infrastructure validation, and the canonical source checks.
[Exact-main CI](https://github.com/harrisonoconnorhover/dander/actions/runs/36849004513) also passed
on that exact merged revision.

Row-level tests execute the generated predicates through a narrow SQLite translation. This is
not live BigQuery proof. Current raw data is recompiled, so staging can scan outside the selected
dates. Existing outputs are required; earlier outputs remain published if a later output fails.
See [output repair](../docs/output-repair.md) for operator instructions and compatibility.
