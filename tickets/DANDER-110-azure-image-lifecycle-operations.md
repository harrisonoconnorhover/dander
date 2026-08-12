# DANDER-110 — Promote and operate Azure Container Apps runtimes

Status: done; merged through protected PR #190 and live-qualified on 2026-08-11

## Requirement

Copy the accepted source-free OCI runtime into ACR without rebuilding or changing its index or
platform-manifest digests. Add provider-native, manifest-bound Container Apps Job start, status,
logs, stop, and replay operations while keeping all live mutations behind explicit confirmation.

## Acceptance

- [x] Promotion requires the accepted `io.dander.runtime.artifact/v1` record and an immutable source
  digest before provider access.
- [x] ACR authentication uses the signed-in Azure identity; no static registry credential enters a
  command, environment, artifact record, or repository file.
- [x] A deterministic tag is copied with `docker buildx imagetools create` and never rebuilt.
- [x] ACR's GA image metadata must report the accepted index digest, and the immutable destination
  must expose the exact accepted platform-manifest map before the Azure artifact record is written.
- [x] Existing deterministic tags are idempotent when equal and fail closed when unequal.
- [x] Lifecycle commands are bound to one manifest-selected job and normalize only known provider
  states.
- [x] Logs use a bounded Log Analytics query correlated to one validated execution name.
- [x] Stop requires a running execution; replay requires a terminal execution and relies on the
  runtime's persisted inclusive cursor.
- [x] A successful stop request reports `cancellation_requested`, not a synthetic terminal state;
  the read-only status command remains authoritative because Azure may settle the execution later.
- [x] Image copy, start, stop, and replay require interactive confirmation in the CLI.
- [x] Focused tests use mocked command runners and make no Azure call.
- [x] Protected CI passed and this ticket merged before any live ACR copy or job execution.

## Evidence boundary

The implementation PR contained deterministic local contract evidence only. The separately
approved Phase 6 run preserved registry digest identity and exercised start, schedule, status,
bounded logs, stop, replay, rollback, cleanup, and no drift. Azure remains experimental pending
Phase 8 qualification.
