# Morning Handoff

## Finished

- Reproduced the isolated Salesforce-plugin proof from public Dander `0.4.0rc2` and plugin `0.1.0rc1` in a source-free project.
- Verified presentation-safe Salesforce discovery and canonical graph API write-back.
- Found that an atomically saved `0600` graph was unreadable by the generated image's UID 65532 runtime.
- Prepared `0.4.0rc3` so generated images copy manifests, connectors, graphs, and models as UID/GID 65532.
- Proved the fix in a Linux container without weakening host file permissions.

## Try It

```bash
uv run pytest tests/project/test_scaffold.py tests/pipeline/test_graph_deployment.py
uv run dander new /tmp/dander-rc3-project
```

## Checks

- Ruff lint/format, strict mypy, and all 691 tests passed.
- Dependency audit reported no known vulnerabilities.
- Terraform format, backend-disabled initialization, and validation passed for platform and stage zero.
- Wheel and sdist installed outside the checkout, generated valid projects, and contained the corrected Dockerfile.
- A host `0600` graph validated inside the Linux image as UID 65532.

## Decisions

- Preserve restrictive host graph permissions; fix only container ownership at the source-free packaging boundary.
- Treat public `0.4.0rc2` as a failed candidate and require `0.4.0rc3` before resuming acceptance.
- Keep the retained proof project untouched.

## Remaining

- Merge the protected `0.4.0rc3` candidate PR after CI.
- Obtain explicit approval before tagging or publishing `0.4.0rc3`.
- Increase the Google account's project quota; the requested disposable project was not created.
- Create, plan, and apply only the paused disposable Salesforce proof after quota is available.
- Complete Druff execution, replay, state/cleanup checks, and final no-drift plan.

## Review First

- `src/dander/templates/project/Dockerfile`
- `tests/project/test_scaffold.py`
- `CHANGELOG.md`
