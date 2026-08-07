# Morning Handoff

## Finished

- Made GCP planning compile one execution projection per version 1 pipeline.
- Made Cloud Run consume projected image, command, resources, schedule, environment, and secrets.
- Added Terraform identity, observability, and unsupported-capability preconditions.
- Switched hosted commands to `runtime execute` while preserving model catalog output and safety.
- Preserved every existing Terraform resource address and plan-first CLI input.

## Try It

Run the usual `dander init-platform-plan` with an immutable image. The saved plan now receives
validated `io.dander.execution/v1` objects; direct hand-authored execution projections are not a
supported operator path.

## Checks

- Ruff format/lint and strict mypy passed.
- All 815 tests passed.
- Root and stage-zero Terraform formatting and validation passed.
- Focused projection/bootstrap/runtime tests passed with exact command and limit assertions.

## Decisions

- Python is the projection compiler; Terraform validates and maps the resulting immutable intent.
- Cloud Run native execution variables remain the source for dynamic run IDs, attempts, and shards.
- Existing direct Terraform input examples are reference-only; the supported plan path is the CLI.

## Remaining

- Merge this focused ticket through protected main.
- Publish a source-free candidate and run local/isolated Cloud Run normalized-outcome parity.
- Prove signal, replay, cleanup, and final no-drift behavior before closing Phase 1.

## Review First

- `src/dander/deployment/projection.py`
- `src/dander/bootstrap/terraform.py`
- `infra/modules/scheduled-job/main.tf`
