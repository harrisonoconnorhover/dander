# Morning Handoff

## Finished

- Completed a real PostgreSQL Control → Cloud Run → BigQuery workflow: 18 ingested jobs and
  38 published rows matching the stored source, with no missing, unexpected, or duplicate keys.
- Verified crash recovery without duplicate execution, cancellation before worker startup,
  durable terminal histories after restart, and completion logs through the API.
- Fixed standalone Google authentication and cancellation without a provider completion timestamp;
  merged through protected PRs [#531](https://github.com/harrisonoconnorhover/dander/pull/531) and
  [#532](https://github.com/harrisonoconnorhover/dander/pull/532).
- Added the runnable Cloud Run profile preparation example, reusing canonical project/plan types.
- Removed the temporary Job, image, PostgreSQL container, process, and database credentials.
  Saved the database snapshot and results in local `tmp/postgresql-gcp-20260907`; retained jobs
  stayed unchanged and all five schedules stayed paused.

## Try It

Run `uv sync --frozen --extra dev --extra postgres`, then
`uv run dander control serve --profile examples/control/local.yaml` for the local API.
Follow [Control profiles](docs/control-profiles.md) to prepare an existing Cloud Run deployment
with `examples/control/prepare_cloud_run.py`. The temporary acceptance environment is removed.

## Checks

- 44 focused identity/Google backend tests, 15 profile/identity tests, and 38 Cloud Run/lifecycle
  tests passed, including PostgreSQL recovery. Ruff and strict typing passed across 492 files.
- All six exact-main CI checks passed for worker source `a548d74` and final Control `98d4186`:
  [final run](https://github.com/harrisonoconnorhover/dander/actions/runs/34161045039).
- Live API/provider reconciliation confirmed exactly two executions and persistent terminal
  results. Output comparison, lease/staging checks, and resource-removal checks passed.
- Documentation links, handoff format, completed-ticket checks, and `git diff --check` passed.

## Decisions

- Keep explicit AWS federation strict; use Google Application Default Credentials otherwise.
- Preserve the private RC22 trial and the original HDFS branch. This evidence covers one local
  Control process with PostgreSQL 17 and one public Greenhouse graph.

## Remaining

- Attribute existing AWS spending and idle resources before further paid work; the conservative
  monthly reservation is close to the USD 25 ceiling.
- Add operation-level telemetry to the legacy graph path before treating byte counters as costs.
- Secure Hadoop work needs an existing cluster and access details.
- DANDER-207 scale/cost, pairwise, other-profile soak, audit, and release gates remain open.

## Review First

- [Workflow results and limits](tickets/DANDER-283-postgresql-gcp-workflow.md)
- [Runnable profile setup](docs/control-profiles.md)
- [Cancellation regression](tests/control/test_cloud_run_execution_backend.py)
