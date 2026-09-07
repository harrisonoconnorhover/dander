# Start Control from one profile

These commands describe current source, not the public `0.9.0rc20` package. Control manages
graphs and runs; the selected execution backend still runs the existing Dander worker.

## Local first run

From this repository:

```bash
uv sync --frozen --extra dev --extra postgres
uv run dander control serve --profile examples/control/local.yaml
```

The example starts the loopback API on port 8770 with an ephemeral graph store and project `demo`.
It does not connect to a provider or run a pipeline. Stop with Ctrl-C. For local graph persistence,
copy the profile, remove `ephemeral`, and set `root` to a private directory.

Profile keys are the long command options without `--`. Repeated options use YAML lists. Paths
are relative to the profile file, so it can be invoked from another working directory. Explicit
command-line options take precedence regardless of their position:

```bash
uv run dander control serve --profile examples/control/local.yaml --port 8771
```

The profile uses the same option and startup validation as a direct invocation. Unknown keys and
invalid option combinations are rejected before the server starts. An external network bind still
requires the existing OIDC configuration. Profiles never expand environment variables into values;
credentials stay in the existing referenced environment variables or provider identities.

## PostgreSQL run storage

PostgreSQL Control stores graphs, run snapshots, attempts, and optional schedule state in one
dedicated schema. It can register existing Cloud Run or Dataproc plans without requiring Fargate,
S3, SQS, or AWS credentials. The existing AWS flags remain available for compatibility.

For one existing version 1 Cloud Run graph pipeline, the
[preparation example](../examples/control/prepare_cloud_run.py) creates the graph in PostgreSQL
and writes its canonical plan, startup binding, and profile together. It requires the `postgres`
extra, a dedicated database schema, Google credentials, and the immutable image of a job already
deployed from the same project configuration:

```bash
uv run python examples/control/prepare_cloud_run.py \
  --config /path/to/project/dander.yaml \
  --pipeline greenhouse_jobs_graph \
  --gcp-project-id your-existing-project \
  --image 'us-central1-docker.pkg.dev/your-existing-project/dander/dander@sha256:YOUR_DIGEST' \
  --output-dir /path/to/private/control \
  --schema control_example
uv run dander control serve --profile /path/to/private/control/control.yaml
```

Set `DANDER_CONTROL_DATABASE_URL` through your existing secret mechanism first. The example applies
the known migrations and registers the graph; it does not create or execute a cloud job. Repeating
the same configuration reuses the graph and plan. Use a separate schema and output directory for
a different deployment; retain the original database and plans for its run history. The manifest's
job name, service account, image, command, and task bounds must match the deployed job. A worker
used with current Control must emit the current `runtime.completed` telemetry; the retained RC22
worker predates that result format.

With that example running, submit the graph from another terminal:

```python
import httpx

base = "http://127.0.0.1:8770"
graph_url = base + "/v1/projects/demo/graphs/greenhouse-jobs-graph"
with httpx.Client(timeout=45) as client:
    graph = client.get(graph_url)
    graph.raise_for_status()
    run = client.post(
        graph_url + "/runs",
        headers={"If-Match": graph.headers["etag"], "Idempotency-Key": "greenhouse-example-0001"},
    )
    run.raise_for_status()
    print(run.json())
    print(base + "/v1/runs/" + run.json()["run_id"])
```

Run the snippet with `uv run python`. Repeating its idempotency key returns the same run; choose
a new key only to request another execution. The printed status URL contains state, row counts,
and telemetry. `/v1/runs` lists history, and `/v1/runs/{run_id}/logs` reads execution logs. Pass each
response's `next_cursor` as `cursor` until it is null; a provider page may be empty and still have
a continuation cursor. To cancel, POST an empty body to `/v1/runs/{run_id}/cancel` with a new
`Idempotency-Key`. Wait for `canceled` before treating cancellation as complete. Restart Control
with the same profile and database to
resume observing an accepted execution.

An operator supplies a PostgreSQL connection through `DANDER_CONTROL_DATABASE_URL` using the
existing secret mechanism. The startup binding contains only that variable's name and the schema.
Generate its canonical JSON once:

```python
from pathlib import Path
from dander.control.startup_bindings import (
    PostgreSQLRunStoreStartupBinding,
    serialize_run_store_startup_binding,
)

Path("control/run-store.json").write_bytes(
    serialize_run_store_startup_binding(
        PostgreSQLRunStoreStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        )
    )
)
```

Create the `control` directory first. Put the profile beside your existing `dander.yaml`:

```yaml
config: dander.yaml
platforms-config: dander.platforms.yaml
project: [analytics]
execution-plan: [control/plans/greenhouse.json]
run-store-config: control/run-store.json
run-environment: gcp
gcp-project-id: your-existing-project
```

The execution plan must be the canonical plan for an existing graph and deployed job; this profile
does not provision a job, create credentials, or compile an arbitrary pipeline. It replaces the
repeated startup arguments while retaining their existing immutable inputs.

For PostgreSQL schedules, add `trigger-spec` and `schedule-source-config`. Generate the latter
with `PostgreSQLScheduleSourceStartupBinding` and `serialize_schedule_source_startup_binding`,
using the same connection-variable name and schema as run storage. Five-field cron and IANA time
zones are supported. A PostgreSQL scheduler leader emits the existing durable wakeups; direct
provider schedules must remain paused when Control owns execution.

## Operating boundary

Use one Control process per run-store schema for this integration. PostgreSQL durability and
scheduler leadership do not establish multiple-replica run reconciliation or a supported hosted
topology. Startup applies the known schema migrations before accepting traffic; take normal
database backups and preserve the corresponding canonical plan files. No automatic data-store
migration from an existing S3 profile occurs.

## Google credentials

Standalone Control uses Google's Application Default Credentials for Cloud Run, Dataproc, and
BigQuery metadata. For a local operator, initialize them with
`gcloud auth application-default login`; on Google infrastructure, use the attached service
account. Set `gcp-project-id` explicitly in the profile so a different CLI default project cannot
select the execution destination. Credentials are not stored in the YAML or canonical plan.

AWS-hosted Control retains its existing Fargate federation path. If any of
`DANDER_GCP_WIF_AUDIENCE`, `DANDER_GCP_SERVICE_ACCOUNT`, or
`AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` is present, that path must be fully configured; incomplete
federation never falls back to another Google account. Credential failures return a sanitized
error without provider exception details.

## Observed workflow

On September 7, 2026, [DANDER-283](../tickets/DANDER-283-postgresql-gcp-workflow.md) exercised the
public Greenhouse graph using local PostgreSQL 17, one Control process, native Google credentials,
and a temporary Cloud Run Job. The worker came from main `a548d74`; final Control reconciliation
used main `98d4186`. Both revisions passed their exact-main CI checks.

A forced Control restart adopted the same execution, and repeating its request key returned the
same run. Ingestion processed 18 jobs; the graph's 38 published rows matched the retained source
exactly. A second execution canceled before startup. Both terminal run histories survived another
restart, and the successful completion event was readable through API logs. The temporary Job,
image, database container, and Control process were removed after observation; the five retained
operator schedules stayed paused.

This is one named Cloud Run workflow. Local tests cover additional PostgreSQL persistence,
claims, scheduling, and startup behavior using fake execution backends. Neither establishes
secure Hadoop, Kubernetes, multiple-replica Control, or broader release qualification. The legacy
graph emitted no operation-level telemetry; its zero byte counters do not establish zero billing.

## Job measurements

Current-source workers preserve completed BigQuery graph queries and SCD1 ingestion load/query
statistics in `runtime.completed`. The existing Control result summary exposes their operation
count, rows, bytes processed/billed, duration, and retry count. This also covers SCD1-derived
incremental writes. The change is tracked in
[DANDER-284](../tickets/DANDER-284-bigquery-graph-job-telemetry.md); it does not rewrite the earlier
DANDER-283 observations or update an already deployed image.

These counters cover the recorded successful jobs, not the whole provider bill. Other legacy
write modes, non-graph model execution, state/catalog work, retained infrastructure, and storage
are outside this collector. BigQuery's pricing mode, discounts, and credits also affect cash
charges; use provider billing to reconcile actual spending.
