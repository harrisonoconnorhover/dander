# Morning Handoff

## Finished

- Added a lazy, explicitly configured AWS Glue Data Catalog provider.
- Published canonical relation, schema, lineage, tests, metrics, ownership, and sensitivity through
  direct database/table APIs with deterministic readback.
- Preserved unrelated database, table, storage-descriptor, partition, and column metadata.
- Replaced provider-neutral executor/runtime use of “Dataplex publisher” with a generic catalog
  publisher while retaining the legacy constructor and CLI compatibility inputs.
- Added focused configuration, lazy-loading, create/update/readback, preservation, and sanitization
  coverage plus operator documentation.

## Try It

Select `catalog.provider: glue` in a version 2 platform profile as shown in `docs/aws-glue.md`, set
`publish_catalog: true` on a model pipeline, and run with an ambient AWS identity. This slice has
not contacted AWS and is not a support claim.

## Checks

- Ruff, 34 focused tests, strict mypy across 303 files, and the full 1,094-test suite passed;
  the full suite included ephemeral PostgreSQL 15 integration.
- Wheel, sdist, source-free installation, runtime-all installation, dependency audit, and the final
  non-root/read-only container conformance checks passed.
- GCP/AWS Terraform validation and tests plus Helm lint/template passed. The retained GCP project
  produced exactly `No changes.` using its reviewed 600-second runtime override; no apply ran.

## Decisions

- Glue databases encode canonical catalog and namespace coordinates; a digest is added only when
  lowercase normalization would be lossy.
- Dander owns descriptions, columns, classification, and `dander.*` parameters; unrelated metadata
  is retained and catalog objects are never deleted.
- Glue uses direct APIs and ambient AWS identity; crawlers, IAM provisioning, Lake Formation, tags,
  live proof, and support promotion remain separate gates.

## Remaining

- Let protected CI repeat Linux tests, packaging, container scanning, and secret scanning.
- Reconcile the tracked 300-second job default and retained 600-second operator override in a
  separate change; do not mix it into the Glue catalog slice.
- Continue the cloud-portability plan in a separate branch after merge; do not deploy or mutate
  AWS/GCP resources in this slice.

## Review First

- `src/dander/catalog/glue.py`
- `src/dander/providers/glue/`
- `tests/catalog/test_glue.py`
