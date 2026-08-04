# Morning Handoff

## Finished

- Added an opt-in deployment preview for a clean, saved graph revision.
- Built and pushed an immutable source-free candidate before planning the complete manifest.
- Rendered the exact no-color Terraform plan from an isolated temporary workspace.
- Kept browser inputs limited to the graph ETag; the operator fixes every cloud input at startup.
- Preserved file-only save, existing job execution, and the canonical applyable Terraform plan.

## Try It

```bash
uv run dander graph serve --file graphs/greenhouse_jobs.yaml --config dander.yaml \
  --pipeline greenhouse_jobs_graph --project YOUR_PROJECT \
  --enable-deployment-preview --failure-alert-email YOUR_ALERT_EMAIL \
  --billing-account YOUR_BILLING_ACCOUNT
```

Open the graph in Druff, refresh status, then choose **Build candidate & plan**.

## Checks

- Ruff lint/format and strict mypy passed; all 654 Python tests passed.
- Both Terraform roots initialized without backends and validated successfully.
- Wheel/sdist build and inspection passed; the dependency audit found no known vulnerabilities.
- Live proof pushed candidate `sha256:c9c16f8ac162...` and rendered `0 add, 5 change, 0 destroy` for the five shared-image jobs.
- No apply, deployment, schedule, state, dataset, IAM, or secret mutation occurred; deployed jobs retained their prior image digest.

## Decisions

- Save remains file-only; candidate creation is a separate explicit action.
- The candidate snapshot includes connectors, graphs, models, and manifest, but never repository source.
- Repeatable operator-only `--secret-id` inputs preserve extra managed containers; preview plans remain temporary and non-applyable.

## Remaining

- Push and open a focused PR only after explicit approval.
- Let protected CI repeat Python, Terraform, package, dependency, and secret checks.
- Treat deployment/apply as a separate, explicitly approved action.

## Review First

- `src/dander/pipeline/graph_deployment.py`
- `src/dander/cli/main.py`
- `tests/pipeline/test_graph_deployment.py`
