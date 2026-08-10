---
id: DANDER-103
title: Add shared bounded columnar staging
status: completed
component: warehouse
epic: cloud-portability
created: 2026-08-08
---

# DANDER-103 — Shared bounded columnar staging

## Scope

- Map canonical schema v1 into Arrow/Parquet without importing provider SDKs at Dander startup.
- Consume one row iterator into bounded row/byte parts with Zstandard or Snappy compression.
- Record relative filenames, counts, compressed size, schema fingerprint, and SHA-256 checksums.
- Clean only the exact run-scoped local directory on normal or handled-failure exit.

## Acceptance

- Scalar, decimal, temporal, JSON, array, and record values round-trip through Parquet.
- Row and logical-byte thresholds split an input without endpoint-wide materialization.
- Files are owner-only and manifest JSON contains no absolute path or row value.
- Invalid schema/records fail with row index only and handled failures remove partial artifacts.
- Snowflake and Redshift extras explicitly install the shared PyArrow dependency.

## Exclusions

No S3/Snowflake upload, remote stage, warehouse `COPY`, target mutation, cloud credentials, support
promotion, or live provider call belongs in this ticket.
