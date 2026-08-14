# Morning Handoff

## Finished

- Preserved private RC22 Phase 8 metadata and the unchanged public RC20 boundary while rebasing.
- Added a separate deterministic Kubernetes hosted-Control chart; the runtime CronJob chart is unchanged.
- Reused D6 service/OIDC/local GraphStore contracts with exact active and rollback image values.
- Added a single-writer PVC, hardened pods, ingress-nginx TLS routing, and token-free identities.
- Added closed rendering, Helm preflight, read-only verification, focused tests, and DANDER-129.

## Try It

Copy `infra/kubernetes/chart/dander-control/kubernetes-control-plane.example.json`, then run the
render and preflight commands in the adjacent README. Review the saved Helm manifest before apply.

## Checks

- Repository Ruff lint/format and Control contract-drift checks passed.
- Focused strict mypy passed; full mypy reached four existing SDK-version errors in untouched files.
- Full pytest passed: 1,686 tests, 28 skipped, one upstream deprecation warning.
- Both Helm charts linted/rendered; wheel and source-distribution inspection passed.
- The completion-review correction fixes and verifies ingress-nginx's request limit at 6 MiB.

## Decisions

- This profile is one replica plus `ReadWriteOnce`; it makes no HA or horizontal-scale claim.
- Existing-cluster Helm revision/render replaces inapplicable Terraform state and plan ceremony.
- Keep the private RC22 Phase 8 lane separate from D7 Kubernetes Control qualification.

## Remaining

- Merge the focused protected implementation PR and verify exact-main CI.
- Run the free disposable kind HTTPS/OIDC/persistence/rollback/cleanup qualification.
- Record only sanitized evidence in a separate PR and close DANDER-129 if it passes.
- Continue RC22 Phase 8 qualification independently within its existing authorization.

## Review First

- `src/dander/deployment/kubernetes_control_plane.py`
- `infra/kubernetes/chart/dander-control/templates/control.yaml`
- `infra/kubernetes/chart/dander-control/templates/ingress.yaml`
