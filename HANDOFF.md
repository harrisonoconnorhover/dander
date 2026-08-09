# Morning Handoff

## Finished

- Added an experimental Redshift warehouse provider using native database/schema coordinates.
- Implemented bounded Parquet staging, same-region S3 manifest `COPY`, and IAM-only authentication.
- Added deterministic SCD1 merge/replay, additive schema checks, transactional destination fencing,
  and exact owned-object cleanup.
- Registered only BigQuery-state/Redshift and PostgreSQL-state/Redshift as experimental pairs.
- Updated existing compatibility, staging, limitations, and architecture decision records.

## Try It

Configure a v2 `warehouse.provider: redshift` profile with provisioned-cluster or Serverless IAM
settings, then use the normal run path with SCD1. No AWS live profile was created or contacted.

## Checks

- Ruff, formatting, and strict mypy passed; all 1,063 tests passed with PostgreSQL 15 integration.
- Wheel/sdist build, inspection, outside-checkout installs, source-free generation, and full runtime
  dependency loading passed; strict dependency audit found no known vulnerabilities.
- The exact final container built and passed non-root, read-only, runtime, and bundled-asset checks.
- GCP/AWS Terraform roots and module tests, cross-cloud feasibility roots, and Helm checks passed.

## Decisions

- Redshift accepts ambient AWS credentials and uses a configured IAM role for `COPY`; no static
  access keys or database passwords enter configuration or SQL.
- Publication is SCD1-only and transactionally fenced; replay identity includes target, pipeline,
  run, schema fingerprint, and deterministic manifest digest.
- Unsupported transforms, graphs, write modes, and semi-structured mappings fail closed.

## Remaining

- Let protected Linux CI repeat image/config/secret scans before merge consideration.
- Resolve the pre-existing retained GCP no-drift baseline before merging provider work.
- Qualify Redshift transforms, other write modes, and one live disposable AWS profile separately.

## Review First

- `src/dander/providers/redshift/fence.py`
- `src/dander/providers/redshift/writer.py`
- `tests/providers/test_redshift_warehouse_runtime.py`
