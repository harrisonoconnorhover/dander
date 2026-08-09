# Morning Handoff

## Finished

- Made `RelationRef(catalog, namespace, name)` the dependency-light coordinate passed through run,
  graph, transform, metadata, and writer orchestration.
- Moved BigQuery project/dataset and PostgreSQL database/schema translation to provider boundaries.
- Kept legacy CLI and Python compatibility inputs while translating them before neutral execution.
- Fixed non-default raw namespaces so ingestion, transforms, and metadata use the same relation.
- Kept mixed-provider state coordinates independent and rejected BigQuery-only safety/Dataplex
  operations before external clients are created.

## Try It

Run an existing BigQuery command unchanged, or select a PostgreSQL profile. Neutral internals now
receive canonical relations; provider configuration still uses its native vocabulary.

## Checks

- All 1,048 tests passed with PostgreSQL 15; Ruff and strict mypy passed.
- Wheel/sdist build, inspection, outside-checkout installs, generation, and validation passed.
- Source-free container conformance, proof assets, dependency audit, and Helm checks passed.
- Repository and generated-project GCP/AWS Terraform validation and module tests passed.

## Decisions

- Canonical relations are the neutral contract; provider-shaped names are compatibility aliases.
- Provider configs own native-to-canonical translation; provider runtimes own translation back.
- Serialized v1/GCP catalog output and resource identities remain unchanged.

## Remaining

- Run protected Linux CI, image/secret scans, and merge the focused PR if clean.
- Resume Snowflake and Redshift runtime work only after this cleanup merges.

## Review First

- `src/dander/cli/run_command.py`
- `src/dander/runtime.py`
- `src/dander/pipeline/runtime.py`
