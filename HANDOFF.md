# Morning Handoff

## Finished

- Added the immutable provider-neutral Control service request/template and lazy service kind.
- Derived hosted command and origins from the existing frozen OIDC deployment input.
- Added closed credential-free local/GCS/S3/Azure/OCI GraphStore locator contracts.
- Wired the typed locator into `control serve`; cloud bindings cannot silently use local disk.
- Kept Druff static artifact identity separate and left every job launcher unchanged.

## Try It

Run `uv run pytest -q tests/deployment/test_control_service.py
tests/control/test_graph_store_factory.py`; inspect a request's `as_dict()` to see the deterministic
provider-neutral deployment input and startup selector.

## Checks

- Focused service, startup, CLI, registry, launcher, and projection tests pass: 63 tests.
- Full suite passes: 1,637 tests, 28 skips; Ruff, format, and strict mypy pass.
- Generated `io.dander.control.contracts/v1` validation passes without drift.
- Protected PR CI run `31806362480` passed all five jobs on implementation commit `395a18d0`.
- The second adversarial review's startup-selection blocker is corrected; no third pass was run.

## Decisions

- D6 defines the portable contract/startup seam; D7 owns provider service/Terraform rendering.
- Hosted origins have one authority: `HostedOIDCDeploymentInput`.
- GraphStore bindings are a closed typed union, never a generic extension mapping.

## Remaining

- Merge the focused protected PR and verify exact-main CI.
- Continue D7 without changing job launchers or publishing an artifact.

## Review First

- `src/dander/deployment/service.py`
- `tests/deployment/test_control_service.py`
- `src/dander/providers/registry.py`
