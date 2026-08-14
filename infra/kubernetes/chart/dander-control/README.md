# Dander hosted Control existing-cluster chart

This separate chart deploys one non-HA hosted Control instance and one Druff static instance to an
existing Kubernetes 1.27+ cluster. It intentionally does not change the runtime CronJob chart.

Copy `kubernetes-control-plane.example.json`, fill only its non-secret coordinates, and render it:

```console
uv run python -m dander.deployment.kubernetes_control_plane render \
  --input .dander/kubernetes-control-plane.json \
  --output .dander/kubernetes-control-plane
uv run python -m dander.deployment.kubernetes_control_plane preflight \
  --input .dander/kubernetes-control-plane.json \
  --output .dander/kubernetes-control-plane
```

Inspect `active-values.yaml` plus the saved Helm render before installing. Images must use
immutable digests. The chart references an existing TLS Secret and never owns credentials or an
identity-provider registration.

The first profile is deliberately single-replica and single-writer. A `ReadWriteOnce` PVC backs
the closed local GraphStore binding, Control uses `Recreate`, and no availability or horizontal
scale claim is made. The Control ServiceAccount may carry provider-reviewed workload-identity
annotations; Druff receives a distinct token-free ServiceAccount.

Only ingress-nginx is accepted by this profile. The Ingress disables access logs so authorization
codes, OIDC state, and rejected query tokens are not written by the front proxy. Operators must
provide an ingress-nginx controller compatible with that annotation and an existing TLS Secret for
the exact browser host. Its fixed 6 MiB proxy request limit preserves Control's accepted 5 MiB
graph document plus envelope without making uploads unbounded. Terraform state, saved plans, and
cloud cost controls do not apply to this existing-cluster Helm deployment.
