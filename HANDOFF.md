# Morning Handoff

## Finished

- Added the validated external platform handoff for Azure Container Apps Jobs.
- Kept each overlay pipeline-scoped and free of Key Vault values.
- Made runtime selection use the deployment name when it differs from the platform name.
- Added focused projection, Terraform-bootstrap, and CLI coverage.

## Try It

Run the focused Azure tests listed in `Checks`.

## Checks

- `uv run pytest tests/providers/test_azure_container_apps_runtime.py tests/bootstrap/test_azure_terraform.py tests/cli/test_init_cli.py -q` passed: 39 tests.
- Full pytest passed: 1,743 passed and 34 skipped.
- Ruff lint/format passed on 451 files; canonical strict mypy passed on 419 files.
- Wheel/sdist validation, Control-contract drift, and diff whitespace validation passed.

## Decisions

- Reuse the existing validated `DANDER_PLATFORMS_CONFIG_JSON` boundary used by AWS.
- Project only manifest coordinates and secret references; secret values remain provider-native.
- Keep this correction separate from the Azure objective and all live mutation.

## Remaining

- Complete focused diff review.
- Push the focused PR, pass protected CI/review, and merge it.
- Cut a later candidate containing this correction.
- Bind Azure qualification in a fresh protected-main branch before any live execution.
- Complete Azure scale, cost, pairwise, soak, and final closure gates.

## Review First

- `src/dander/bootstrap/azure_terraform.py`
- `src/dander/providers/azure_container_apps/runtime.py`
- `tests/bootstrap/test_azure_terraform.py`
