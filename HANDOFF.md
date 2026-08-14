# Morning Handoff

## Finished

- Merged the protected D7 Kubernetes hosted-Control implementation at `95540e8`.
- Verified exact-main CI run 31828316606: all five jobs passed.
- Reproduced the accepted synthetic OIDC image and started the disposable kind qualification.
- Found and fixed the verifier's one-byte served-bootstrap newline mismatch.

## Try It

Run the focused Kubernetes deployment test, then rerun the live verifier against the disposable
cluster. The verifier now compares the served ConfigMap value rather than its file-ending newline.

## Checks

- Exact protected-main CI run 31828316606 passed all five jobs at `95540e8`.
- Deterministic active/rollback preflight passed with equal repeated Helm renders.
- Synthetic OIDC focused upstream tests passed and reproduced patch/image digests.
- First live install reached ready active Control/Druff pods and a bound PVC.

## Decisions

- Preserve strict byte comparison against the ConfigMap-served bootstrap, which omits the file newline.
- Keep the running kind cluster and disposable issuer only until qualification and evidence finish.
- Keep private RC22 Phase 8 work separate from D7 Kubernetes qualification.

## Remaining

- Merge this focused verifier correction and verify exact-main CI.
- Resume HTTPS/OIDC/browser persistence, restart, rollback/restore, and cleanup checks.
- Record sanitized evidence separately and close DANDER-129 only after the live proof passes.

## Review First

- `src/dander/deployment/kubernetes_control_plane.py`
- `tests/deployment/test_kubernetes_control_plane.py`
- `tickets/DANDER-129-kubernetes-control-plane-deployment.md`
