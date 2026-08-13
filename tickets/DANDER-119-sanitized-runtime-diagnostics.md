---
id: DANDER-119
title: Preserve sanitized runtime failure identity
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-118]
created: 2026-08-13
---

## Context

Retained ServiceNow scheduled executions failed on 2026-08-10 and 2026-08-11 with only
`unexpected_error`. The ledger and Cloud Logging contain no safe exception identity, so the
operator-soak diagnosability gate cannot pass.

## Acceptance Criteria

- [ ] Unexpected runtime failures log a bounded exception-class causal chain and status code where
  available, plus run ID, stage, and stable failure code.
- [ ] Exception messages, request/response bodies, credentials, DSNs, source rows, and arbitrary
  object representations never enter the diagnostic.
- [ ] Focused tests prove secret-bearing exception text is absent.
- [ ] A new retained run proves the resulting failure or success is visible and diagnosable.

## Design

Patch the runtime logging path in a focused PR. Do not widen the durable run-history schema or
persist unrestricted exception text.
