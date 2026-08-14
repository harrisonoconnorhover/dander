---
id: DANDER-201
title: Preserve sanitized runtime failure identity
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200]
created: 2026-08-13
---

## Context

Retained ServiceNow scheduled executions failed on 2026-08-10 and 2026-08-11 with only
`unexpected_error`. The ledger and Cloud Logging contain no safe exception identity, so the
operator-soak diagnosability gate cannot pass.

## Acceptance Criteria

- [x] Unexpected runtime failures log a bounded exception-class causal chain and status code where
  available, plus run ID, stage, and stable failure code.
- [x] Exception messages, request/response bodies, credentials, DSNs, source rows, and arbitrary
  object representations never enter the diagnostic.
- [x] Focused tests prove secret-bearing exception text is absent.
- [x] A new retained run proves the resulting failure or success is visible and diagnosable.

## Design

Patch the runtime logging path in a focused PR. Do not widen the durable run-history schema or
persist unrestricted exception text.

## Implementation Notes

- The classifier retains at most eight bounded ASCII exception class names and the nearest numeric
  HTTP/provider status, including botocore's nested response metadata; it never serializes an
  exception message or object representation.
- `PipelineExecutor` writes one deterministic diagnostic JSON record with run, pipeline, stage,
  code, duration, class chain, and status while leaving durable history unchanged.
- The launcher-neutral runtime CLI emits the same sanitized identity when secret resolution,
  provider construction, or other setup fails before `PipelineExecutor` can run.
- A task-local diagnostic checkpoint prevents the CLI from duplicating an authoritative executor
  event without mutating arbitrary provider exception objects.
- Local failure, executor, runtime-CLI, telemetry, and qualification tests pass. Retained-provider
  evidence is recorded in `docs/evidence/phase8/2026-08-14/gcp-diagnostic.json`.
- Exact RC21 execution `dander-servicenow-incidents-7kxl4` retained bounded class chains and one
  numeric status without messages, bodies, credentials, DSNs, or rows. This establishes safe
  causal identity and does not claim the external ServiceNow root cause.
