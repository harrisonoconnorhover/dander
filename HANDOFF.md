# Morning Handoff

## Finished

- Added immutable `io.dander.execution/v1` templates for launcher-neutral hosted intent.
- Separated non-secret environment, secret references, workload identity, and run correlation.
- Separated runtime retry intent from launcher retry intent.
- Added explicit Cloud Run capability/limit validation that fails before planning.
- Added deterministic version 1 manifest projection without changing Terraform or live resources.

## Try It

Load `dander.yaml`, then call `build_gcp_v1_execution_templates()` with an immutable image and GCP
project. Inspect `ExecutionTemplate.as_dict()` or bind a run through `ExecutionTemplate.bind()`.

## Checks

- Ruff format/lint and strict mypy passed.
- All 814 tests passed.
- Root and stage-zero Terraform formatting, backend-disabled initialization, and validation passed.
- Wheel/sdist build and distribution inspection passed.

## Decisions

- Deployment-time templates and run-time correlation are separate immutable objects.
- Secret references never enter the non-secret environment mapping.
- Cloud Run advertises only Dander's current one-task/one-worker behavior; unsupported requested
  fields fail instead of being silently ignored.

## Remaining

- Merge this focused ticket through protected main.
- Make Cloud Run Terraform consume this projection with exact behavior parity.
- Run local/Cloud Run normalized outcome, signal, replay, and no-drift proof.

## Review First

- `src/dander/deployment/projection.py`
- `tests/deployment/test_execution_projection.py`
- `docs/execution-projection.md`
