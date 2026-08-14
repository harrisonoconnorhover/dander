# Morning Handoff

## Finished

- Merged private RC21 preparation through PR #275 at protected main `c10968a`; exact-main CI run `31820352565` passed all five jobs.
- Built and clean-installed the exact RC21 wheel/sdist, then published source-free multi-platform image `sha256:ab24a0b8a938...` with SBOM and provenance.
- Recorded the USD 10 authorization, provider allocations, candidate hashes, and exact GCP diagnostic objectives before live execution.
- Found that multi-deployment bundles could not select a deployment for `runtime inspect`; added the narrow selector locally with focused coverage.

## Try It

Run `uv run pytest -q tests/test_runtime_inspection.py`.

## Checks

- PR #275 exact-head review passed and all conversations resolved; all five protected PR checks passed.
- Exact-main CI run `31820352565` passed all five jobs at `c10968a`.
- Final RC21 wheel/sdist validation, clean full-runtime install, and source-free GCP/Kubernetes config validation passed.
- GAR index verification found AMD64 and ARM64 manifests plus attestations; read-only image `--version` returned `0.9.0rc21`.
- Runtime-inspection tests, focused Ruff/format, and focused strict mypy pass for the local selector fix.

## Decisions

- RC21 is approved only for DANDER-201 retained diagnostic evidence; it is not the final qualification candidate because its multi-deployment inspection failed closed.
- Cut a corrected private candidate after the selector fix merges; do not weaken the one-digest or source-free gates.
- Kubernetes lifecycle-adapter implementation remains excluded because it overlaps Druff.

## Remaining

- Review and merge the runtime-inspection selector, then cut and publish the corrected private candidate.
- Plan/apply RC21 to the retained GCP jobs and run one bounded ServiceNow diagnostic observation.
- Run exact-candidate Kubernetes, GCP, Azure, scale, pairwise, and final audit gates within the USD 10 ceiling.
- Record unavailable AWS/OCI credentials as provider blockers without weakening gates.
- Complete the retained soak through 2026-09-01 before public support release.

## Review First

- `src/dander/runtime_inspection.py`
- `src/dander/cli/runtime_command.py`
- `docs/evidence/phase8/2026-08-14/rc21-candidate.json`
