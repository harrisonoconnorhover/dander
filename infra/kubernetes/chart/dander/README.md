# Dander existing-cluster chart

This chart deploys immutable Dander runtime images to an existing Kubernetes 1.27+ cluster. It
does not create a cluster, database, external secret, registry, monitoring stack, or cloud identity.

Generate reviewed values and manifests with `dander kubernetes plan`. Install only after review:

```console
helm upgrade --install dander infra/kubernetes/chart/dander \
  --namespace dander --create-namespace --values .dander/kubernetes/profile.values.yaml
```

The chart defaults to `concurrencyPolicy: Forbid`, `restartPolicy: Never`, explicit resource
requests/limits, bounded retries/deadlines, job-history limits, and completed-Job TTL cleanup.
Existing Secret values are referenced by name and key; they are never placed in Helm values.

Helm owns the ServiceAccount, ConfigMap, CronJobs, and optional read-only RBAC. It never owns the
referenced Secret. A ConfigMap may explicitly opt into `helm.sh/resource-policy: keep`; the default
is normal uninstall cleanup. Use `helm rollback` for chart rollback and review `helm diff` or a
server-side dry-run before upgrades where those tools are available.
