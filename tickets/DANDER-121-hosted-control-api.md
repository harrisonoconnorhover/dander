---
id: DANDER-121
title: Add the hosted Dander Control API
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-119, DANDER-120]
created: 2026-08-13
---

## Context

Add a separately named hosted service over GraphStore and extracted Dander application operations
without widening the loopback server or duplicating semantics.

## Acceptance Criteria

- [x] Add versioned capabilities; project/graph CRUD; validation/preview; run/status/bounded-log/
      cancel/replay; and connector/plugin/operation catalog routes.
- [x] Reuse canonical graph, compiler, catalog, state, and provider boundaries through normalized
      application ports rather than the current GCP graph wrapper.
- [x] Enforce sizes, pagination, idempotency, optimistic concurrency, safe errors, correlation,
      health/readiness, graceful shutdown, and lazy selected-provider imports.
- [x] Preserve loopback behavior and pass hosted in-memory/local multi-graph tests.
- [x] No provider payload, credential, secret value, SQL, row, or unrestricted log is exposed.

## Design

Current Cloud Run/BigQuery graph status/run/preview code remains a local compatibility adapter.
Hosted lifecycle and planning use provider-neutral application DTOs and capability diagnostics.

## Implementation Notes

- Added immutable GraphStore-backed application ports for canonical validation, deployment
  preview, and global run lifecycle. Selected adapters own successful mutation replay and
  conflicting idempotency-key rejection; the HTTP layer does not keep a temporary ledger.
- Added the minimum project, graph, catalog, validation, preview, run-history, bounded log, cancel,
  and replay surface. Unwired operations are omitted from capabilities and fail closed.
- Added additive project-list, graph-create/resource/page, and run-page transport contracts. The
  source bundle digest is `e88f732308db41872d0438b9b79df345647c4552a1c750e0230515939d09a246`;
  it is not public until a separately approved release.
- Strong ETags reversibly encode opaque revisions. Graph requests stream to a fixed limit,
  responses and pages are bounded, errors are correlation-safe, and mutation logs exclude bodies,
  headers, identifiers, and secret values.
- Added `dander control serve` with durable rooted-local or explicit ephemeral storage. It rejects
  non-loopback binds before server construction until DANDER-126 supplies OIDC. Its lightweight
  console dispatch path does not import any provider SDK before command selection.

## Review Log

- 2026-08-13: pre-implementation adversarial review blocked raw opaque revisions in HTTP headers,
  unspecified run idempotency/revision ownership, and post-buffer body-size checks. The design now
  base64url-wraps ETags, assigns lifecycle idempotency to selected adapters, and streams at most the
  configured limit plus one byte.
- 2026-08-13: completion review blocked a changed meaning for the published v1 `RunRequest` and
  eager provider imports through the real console entrypoint. Run start now uses `If-Match` and
  `Idempotency-Key` headers with the published DTO restored exactly, and a focused dispatcher keeps
  `dander control serve` provider-free while preserving the existing CLI for other commands.
