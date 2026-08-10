---
id: DANDER-105
title: Qualify the experimental Redshift warehouse
status: completed
component: warehouse
epic: cloud-portability
depends_on: [DANDER-103]
created: 2026-08-09
---

## Acceptance Criteria

- [x] Map native Redshift database/schema coordinates into canonical relations.
- [x] Load bounded compressed Parquet parts through same-region S3 manifest `COPY` with IAM roles.
- [x] Select bounded direct staging only through explicit row and logical-byte thresholds.
- [x] Implement replace, SCD1, SCD2, snapshot, and incremental publication through the exact
      destination-fence transaction.
- [x] Reject schema drift and unsupported types before remote staging or destination mutation.
- [x] Support explicit strict JSON-to-`SUPER` fallback without permitting it as row identity.
- [x] Render fenced table/incremental transforms, assertions, and provider-neutral replace graphs.
- [x] Emit bounded, sanitized operation telemetry without inventing unavailable query identifiers.
- [x] Prove replay, monotonic cursors, stale ownership, concurrent claims, readback, and cleanup in a
      disposable Redshift Serverless qualification under an approved cost ceiling.
- [x] Pass protected CI after the live-discovered SQL corrections merged.

## Boundary

Redshift remains experimental. Views, ARRAY/RECORD fallback, provisioned-RA3 live qualification,
reusable provider-managed infrastructure, support promotion, and Phase 8 scale/cost qualification
remain outside this completed adapter ticket. The shared four-warehouse result comparison is the
separate re-baselined Phase 5 exit gate.
