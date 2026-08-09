# Kubernetes existing-cluster launcher

Dander packages a versioned Helm chart for an existing Kubernetes 1.27+ cluster. This is a
locally validated portability path, not yet a supported hosted profile. The chart does not create
a cluster, database, registry, external Secret, monitoring stack, or cloud identity.

## Configure one deployment

Keep logical pipelines in the version 2 `dander.yaml`. Select the native PostgreSQL profile and
Kubernetes launcher in `dander.platforms.yaml`:

```yaml
version: 1
platforms:
  postgres:
    warehouse:
      provider: postgresql
      database: dander
      dsn_env: DANDER_POSTGRES_DSN
    state:
      provider: postgresql
      authority_id: kubernetes:analytics
      dsn_env: DANDER_POSTGRES_DSN
    catalog:
      provider: none
    secrets:
      provider: environment

deployments:
  analytics_cluster:
    platform: postgres
    launcher:
      provider: kubernetes
      context: my-existing-context
      namespace: dander
      release_name: dander
      service_account_name: dander-runtime
      existing_secret_name: dander-runtime
      workload_identity_annotations: {}
      pod_labels:
        team: analytics
    runtime:
      cpu: 1
      memory: 512Mi
      timeout_seconds: 900
      max_retries: 1
      batch_rows: 10000
    safety:
      require_guarded_free_tier: false
    pipelines:
      greenhouse_jobs:
        schedule: "0 9 * * *"
        time_zone: America/New_York
        paused: true
        secret_bindings:
          DANDER_POSTGRES_DSN: postgres-dsn
```

`environment` means the launcher injects an environment variable from an operator-owned
Kubernetes Secret. The manifest stores only the key name. Create the Secret separately in the
selected namespace; Helm never owns or reads its value. Workload-identity annotations and pod
labels are plain Kubernetes metadata, so the chart does not encode GKE, EKS, AKS, or OKE policy.

## Plan before install or upgrade

Use an immutable image digest. Planning lints the packaged chart and saves the exact rendered
values and manifests without contacting the cluster:

```bash
dander kubernetes plan \
  --deployment analytics_cluster \
  --container-image registry.example/dander/runtime@sha256:DIGEST
```

Review both files under `.dander/kubernetes/`. The command prints the exact `helm upgrade
--install` command as the next mutating step; run it only with an identity authorized for the
selected context and namespace. New schedules should remain paused through their first manual run.

After installation, compare the ServiceAccount, ConfigMap, CronJobs, schedule/pause settings,
overlap policy, and image digest with read-only calls:

```bash
dander kubernetes verify \
  --deployment analytics_cluster \
  --expected-image registry.example/dander/runtime@sha256:DIGEST
```

## Operate and recover

Create a manual execution from the paused CronJob so it uses the same pod template:

```bash
kubectl --context my-existing-context --namespace dander \
  create job --from=cronjob/dander-greenhouse-jobs dander-greenhouse-manual-001
kubectl --context my-existing-context --namespace dander \
  logs --follow job/dander-greenhouse-manual-001
```

Repeated creation with the same Job name fails rather than starting a duplicate. CronJobs use
`concurrencyPolicy: Forbid`; Dander's durable lease remains the final defense against manual or
cross-launcher overlap. Jobs use `restartPolicy: Never`, bounded retry/deadline settings, a
read-only filesystem with writable bounded `/tmp`, explicit requests/limits, SIGTERM handling,
history limits, and completed-Job TTL cleanup.

Use `helm history` and `helm rollback` for a reviewed chart rollback. A normal uninstall removes
Helm-owned ServiceAccounts, ConfigMaps, CronJobs, and optional RBAC, but never the referenced
Secret, database, Dander state, or warehouse data. `configMap.keepOnUninstall` is an explicit
escape hatch and defaults to `false`; retained objects become the operator's responsibility.
