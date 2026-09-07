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

Local tests exercise PostgreSQL persistence, claims, scheduling, and startup using fake execution
backends. They do not establish secure Hadoop, Kubernetes, or live GCP execution qualification.
