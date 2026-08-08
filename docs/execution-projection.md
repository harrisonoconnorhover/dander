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

The Fargate provider can build the same BigQuery data-plane intent with an immutable ECR image,
AWS task-role identity, `awsvpc` placement, CloudWatch destinations, and explicit Fargate resource
limits. At runtime, Dander accepts only temporary credentials obtained from the fixed ECS task-role
endpoint, then prepares a non-secret Google external-account configuration before any provider
client is constructed. The Google impersonated token is limited to 600 seconds. Because this first
runtime bridge does not yet renew the copied ECS session, Fargate projections are limited to one
hour.

This provider projection is an internal construction gate: provisioning, controller lifecycle,
renewable credential supply, and live acceptance must pass before Fargate is a supported launcher.
