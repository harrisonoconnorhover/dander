# Morning Handoff

## Finished

- Added a read-only comparison of an unsaved graph with its exact saved revision, including downstream outputs and write effects.
- Added readable explanations of recorded run outcomes and eligible next actions; unavailable measurements remain unknown.
- Exposed both operations through existing Control authentication and regenerated their public contracts.

## Try It

With Control running and a saved graph selected, POST candidate graph JSON to `/v1/projects/{project}/graphs/{graph}/change-preview` with its saved `If-Match` ETag. GET `/v1/runs/{run_id}/explanation` for a configured lifecycle's run. See `docs/control-contracts.md` for semantics and client compatibility.

## Checks

- 87 focused comparison, explanation, HTTP, OIDC, and contract tests passed.
- Canonical strict typing passed across 513 files.
- Ruff lint/format, generated Control contract drift, and diff checks passed.
- Protected CI and exact-main verification remain pending for this implementation step. No live cloud workload was run.

## Decisions

- Keep static graph comparison separate from infrastructure deployment preview; no new persistence or provider reads.
- Explain existing run evidence deterministically, with no new AI service or fabricated diagnosis.
- Deliver real date-scoped output repair and the guided interface as subsequent steps of the same active objective.

## Remaining

- Implement bounded output-partition repair from retained raw data, preserving normal extraction progress and unrelated output rows.
- Integrate connect, configure, preview, run, and outcome in Druff; its repository exception is awaiting the user's answer.
- Publish and consume a verified immutable producer artifact for the paired Druff client; existing published clients use their original bundle.
- Complete protected PR, merge, and exact-main CI for each runtime change.

## Review First

- `src/dander/control/change_preview.py` for graph comparison and write explanations.
- `src/dander/control/run_explanation.py` for evidence-based outcome handling.
- `docs/control-contracts.md` for the API and publication boundaries.
