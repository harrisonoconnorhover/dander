# Execution Projection v1

`io.dander.execution/v1` is Dander's immutable, cloud-neutral launcher input. An execution template
contains the OCI digest, runtime command, pipeline/profile identifiers, configuration location,
non-secret environment, secret references, workload identity, resources, separate runtime and
launcher retry counts, task/schedule settings, network intent, labels, and observability targets.

Run IDs, launcher execution IDs, attempts, shards, and deadlines are bound when a launcher starts
an execution. They are validated through the same runtime contract and projected through
`DANDER_*` environment variables. Secret references are kept separately and never appear in that
environment mapping.

Every launcher declares explicit capabilities and limits. Projection validation fails before
planning if the launcher cannot honor requested CPU, memory, deadline, retries, task count,
parallelism, ephemeral storage, network placement, schedule behavior, or extension. `cloud_run`
initially declares only the behavior already supported by Dander's GCP profile: one task, one
parallel worker, the existing CPU/memory/deadline/retry range, schedules with time zones, and no
separately configurable ephemeral storage or network placement.

Version 1 `dander.yaml` manifests compile deterministically to this projection while retaining the
existing GCP/BigQuery compatibility profile. A later configuration migration will separate logical
pipeline intent from environment-specific deployment settings; it does not change this runtime
boundary.

The selected Cloud Run provider supplies these templates to Terraform. Cloud Run consumes their
image, command,
resources, schedule, environment, secret references, identity, labels, and observability contract.
Run-specific IDs and attempts continue to come from Cloud Run's native execution context and are
normalized by the OCI runtime contract.
