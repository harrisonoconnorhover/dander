# Morning Handoff

## Finished

- Added a lazy PostgreSQL 15+ durable-state provider selected from version 2 platform profiles.
- Implemented versioned migrations, server-time leases, monotonic fences, watermark CAS, history, and JSONB metadata.
- Added bounded pooling, terminal-history retention, interruption reconciliation, and fail-closed BigQuery pairing.
- Added live PostgreSQL CI conformance for contention, migration rollback, connection loss, and pool exhaustion.
- Kept version 1 and BigQuery state behavior unchanged.

## Try It

Install `dander-platform[postgres]`, set the manifest's `state.provider: postgresql`, and inject the
DSN through its configured environment variable. See `docs/platform-profiles.md`. Runtime execution
with BigQuery intentionally stops until the next destination-fence ticket is merged.

## Checks

- Ruff, formatting, strict mypy, and all 998 Python tests passed.
- Seven live PostgreSQL 15 provider tests passed; manual container restart recovery passed.
- Terraform formatting, GCP/AWS validation, and provider-mocked AWS tests passed.
- Wheel/sdist inspection, source-free validation, PostgreSQL-extra installation, and OCI build passed.
- Linux OCI uses system `libpq`; non-Linux PostgreSQL installs use Psycopg's binary runtime.

## Decisions

- Manifests retain only the DSN environment-variable name; credentials are runtime-injected.
- Interrupted history is exempt from terminal retention.
- PostgreSQL state does not weaken publication fencing while the cross-backend protocol is pending.

## Remaining

- Run protected CI and merge the focused PostgreSQL-state PR if clean.
- Implement the generic destination target-fence protocol as a separate PR.
- Implement PostgreSQL warehouse capabilities, then the Kubernetes launcher and live profile.

## Review First

- `src/dander/providers/postgresql/state.py`
- `tests/providers/test_postgresql_state_runtime.py`
- `src/dander/project/portable_config.py`
