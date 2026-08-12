# Morning Handoff

## Finished

- Merged the OCI SDK/Vault provider slice through protected PR #212.
- Added a separate lazy, typed OCI Container Instances launcher projection.
- Restricted OCI to the PostgreSQL/PostgreSQL/no-catalog/OCI-Vault profile.
- Added exact OCIR digest, flex-shape, UTC schedule, network, and resource-principal validation.

## Try It

Run `uv run pytest tests/providers/test_oci_container_instances_runtime.py`.

## Checks

- Focused launcher/profile regressions passed: 51 tests.
- The complete pytest suite, Ruff, and repository-wide strict Mypy passed.
- Wheel and source distribution built with the OCI launcher modules included.
- Protected-main CI for the merged Vault slice passed all five jobs.

## Decisions

- OCI retries are whole-task attempts owned by the later reconciler; native restart is `NEVER`.
- OCI OCPUs and fixed ephemeral storage remain explicit provider semantics.
- Live OCI writes remain at $0 pending preflight and a numeric per-attempt ceiling.

## Remaining

- Merge this launcher contract through a protected PR.
- Implement Terraform, then scheduling/lifecycle operations in separate PRs.
- Build one new post-merge candidate and run the complete OCI live gate after account preflight.
- Complete Phase 8 only after Phase 7 passes.

## Review First

- `src/dander/providers/oci_container_instances/runtime.py`
- `src/dander/providers/oci_container_instances/config.py`
- `tests/providers/test_oci_container_instances_runtime.py`
