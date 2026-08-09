# Shared columnar staging

Snowflake and Redshift bulk loaders share one local Parquet artifact contract before their
provider-specific upload and `COPY` steps. This contract does not contact a warehouse or cloud.

`ParquetStagingSession` creates one exclusive run directory, consumes the input iterator once, and
writes Zstandard- or Snappy-compressed parts. A part is bounded by both row count and estimated
logical bytes. One individually oversized row is written alone so the iterator can always make
progress; provider adapters must still enforce their remote object limits.

Each artifact records only:

- its relative filename;
- row and logical/compressed byte counts;
- a SHA-256 content digest.

The manifest also records the run ID and canonical-schema fingerprint. It never contains absolute
paths, DSNs, row values, SQL, credentials, bucket names, stages, or provider account identifiers.
Files and the run directory are owner-only. Normal context exit removes only regular files in that
exact run directory and refuses unexpected nested or symbolic paths.

Canonical scalar, array, record, JSON, decimal, and temporal declarations map explicitly to Arrow.
JSON is stored as stable compact text so each warehouse adapter can choose its own declared native
or string representation. Decimal precision above Parquet/Arrow's 76-digit limit fails before a
file is published. Missing/extra fields, required nulls, malformed JSON, and Arrow conversion errors
produce row-indexed messages without echoing record contents.

This is a prerequisite, not a support claim. Snowflake and Redshift remain unavailable until their
upload, idempotency, target fencing, schema, transform, telemetry, cleanup, and live-profile gates
pass independently.
