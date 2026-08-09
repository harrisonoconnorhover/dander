# Morning Handoff

## Finished

- Added a lazy Kubernetes launcher for version 2 named deployments, using the deployment name
  as the unambiguous runtime selector.
- Packaged a Helm chart for existing Kubernetes 1.27+ clusters in every generated project.
- Added non-mutating `dander kubernetes plan` and read-only drift verification.
- Rendered bounded CronJobs with external Secret refs, `Forbid` overlap, explicit resources,
  deadlines/retries, read-only pods, history limits, and Job TTL cleanup.
- Documented configuration, manual runs, upgrades, rollback, uninstall, and the unqualified boundary.

## Try It

Run `dander kubernetes plan --deployment NAME --container-image REGISTRY/IMAGE@sha256:DIGEST`,
review both saved artifacts, then use the printed Helm command only against an approved cluster.

## Checks

- All 1,022 tests passed with PostgreSQL 15; repository Ruff and strict mypy passed.
- Helm lint/template passed, including optional RBAC and ConfigMap-retention rendering.
- A disposable Kind cluster passed install, read-only verify, manual Job/duplicate rejection,
  upgrade, rollback, and uninstall; its external Secret survived and the cluster was deleted.
- Wheel/sdist inspection, two source-free installs/scaffolds, dependency audit, Terraform
  validation/tests, full-runtime container build, and read-only runtime conformance passed.

## Decisions

- Kubernetes targets an existing cluster; Dander does not create clusters or own external Secrets.
- Operator-managed environment injection is the first Secret path; cloud identity remains metadata.
- Kubernetes/PostgreSQL remains unsupported until the separate live end-to-end profile gate passes.

## Remaining

- Open and merge the focused protected PR after Linux CI and security scans pass.
- Run the native and cross-backend PostgreSQL matrix and bounded/concurrent benchmarks separately.
- Qualify one real existing-cluster PostgreSQL pipeline before changing the support manifest.

## Review First

- `src/dander/providers/kubernetes/operations.py`
- `infra/kubernetes/chart/dander/templates/cronjobs.yaml`
- `docs/kubernetes.md`
