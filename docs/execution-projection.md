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

Launcher factories receive one immutable `ResolvedTemplateRequest` containing only resolved,
provider-neutral projection intent. Provider/data-plane construction values are captured when the
selected factory is built: for example, the Cloud Run and Fargate BigQuery profiles receive their
typed GCP project and guarded-free-tier context there. Kubernetes callers therefore do not invent
empty project identifiers or false GCP guard values. The request contains no generic extension bag,
and its pipeline containers are copied into read-only equivalents before projection.

The selected Cloud Run provider supplies these templates to Terraform. Cloud Run consumes their
image, command,
resources, schedule, environment, secret references, identity, labels, and observability contract.
Run-specific IDs and attempts continue to come from Cloud Run's native execution context and are
normalized by the OCI runtime contract.

The Fargate provider can build the same BigQuery data-plane intent with an immutable ECR image,
AWS task-role identity, `awsvpc` placement, CloudWatch destinations, and explicit Fargate resource
limits. At runtime, Dander accepts only temporary credentials obtained from the fixed ECS task-role
endpoint. A process-local Google credential supplier fetches the current ECS session whenever
Google Auth refreshes its signed AWS subject token; task secrets are neither copied into global
environment variables nor written to disk. The impersonated Google token remains limited to 600
seconds, while the launcher may enforce a deadline of up to 24 hours.

The named Fargate-to-BigQuery/GCP composition has passed complete lifecycle acceptance, including
manual and scheduled execution, replay, interruption, alerts, image rollback, cleanup, and
no-drift reconciliation. Fargate remains experimental until the profile also satisfies the
published scale/qualification objectives; see the
[bounded acceptance record](cloud-portability-fargate-lifecycle-acceptance.md).

The Kubernetes provider projects a selected named profile into a packaged Helm chart for an
existing Kubernetes 1.27+ cluster. Its immutable template uses stdout, operator-owned Secret key
references, optional cloud-neutral ServiceAccount annotations, explicit pod resources, schedules,
deadlines, and bounded retries. The chart adds `Forbid` schedule concurrency and completed-Job TTL
cleanup while Dander leases remain the final overlap defense. Rendering and read-only verification
do not qualify a hosted Kubernetes profile; a real cluster and PostgreSQL acceptance run remain.

The Azure Container Apps Jobs provider projects a selected Azure profile into one UTC-scheduled or
manual job contract. It requires an immutable digest in the selected ACR, one pre-authorized
user-assigned managed identity, exact Container Apps environment placement, supported 1 CPU/2 GiB
or 2 CPU/4 GiB sizing, launcher-owned retries and deadline, Log Analytics destinations, and
versionless Key Vault references for declared secrets. Azure evaluates scheduled-job cron in UTC,
so a non-UTC projection fails before provider access rather than silently changing wall-clock
behavior. Plan-first Terraform now consumes this projection exactly and has provider-mocked
contract evidence; no Azure resource has been created, and live-profile evidence remains a
separate Phase 6 gate.
