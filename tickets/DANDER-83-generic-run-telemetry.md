---
id: DANDER-83
title: Normalize provider-neutral run telemetry
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-82]
created: 2026-08-07
---

## Context

The OCI runtime emitted aggregate ingestion counts but had no shared shape for elapsed time,
provider operation statistics, or cost attribution. Some persisted failure guidance also named
Cloud Run, GCP, or BigQuery even when the stable code was generic.

## Acceptance Criteria

- [x] Whole-run duration is measured with a monotonic clock.
- [x] Operation telemetry normalizes row, byte, duration, retry, and query/job identifiers.
- [x] Cost attribution uses finite non-negative decimals, currency, and an estimated marker.
- [x] Telemetry rejects arbitrary extension payloads and unsafe identifiers.
- [x] Successful, skipped, and failed runtime terminal events include telemetry.
- [x] Existing runtime row metrics and retry/exit behavior remain compatible.
- [x] Failure summaries and structured terminal logs do not assume one cloud or warehouse.
- [x] Focused tests cover validation, aggregation, event output, duration, and redaction.
- [x] Full local validation passes.
- [ ] Protected CI passes.

## Design

Keep telemetry as a small immutable value contract. Core measures elapsed time; provider adapters
will contribute detailed operations when each concrete vertical slice moves behind its capability
boundary. Do not introduce a telemetry service, exporter, or billing estimator.

## Implementation Notes

The runtime-v1 event schema receives additive `outputs.telemetry`; existing metrics remain in
place. Detailed zeroes mean “not reported,” not “free.”

## Review Log

Pending protected review.
