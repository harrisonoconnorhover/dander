# Morning Handoff

## Finished

- Started Phase 1B from stable `v0.7.0` without touching the docs-only roadmap branch.
- Made source-free image publication require one `linux/amd64,linux/arm64` OCI index.
- Added digest-preserving GAR-to-ECR copy verification and a bounded WIF refresh probe.
- Added isolated ECR and manual Fargate proof roots with separate execution/task roles.
- Built one source-free GAR index; both `amd64` and `arm64` passed local runtime conformance.

## Try It

Follow `acceptance/cloud-portability/phase1b/README.md` only with disposable AWS/GCP accounts and
saved reviewed plans. No live apply is required for local validation.

## Checks

- All 825 tests, Ruff, and strict mypy passed.
- Both Phase 1B Terraform roots initialized and validated with locked providers.
- Package build, distribution inspection, and clean external wheel install passed.
- Corrected GAR index `sha256:6cd545fa…be81f` passed both platform conformance and key scans; no
  fixable high/critical CVE was found (four unfixed base-image findings remain visible).

## Decisions

- A registry digest rewrite fails rather than being accepted as an equivalent image.
- Fargate receives no service or schedule; the proof task role has no AWS permission policy.
- Fargate remains feasibility-only until the later portable BigQuery vertical slice.

## Remaining

- Authenticate the new AWS account with short-lived CLI credentials.
- Build/copy the source-free index and run the reviewed live Fargate/WIF refresh proof.
- Run image/config/state/log scans and Cloud Run artifact parity.
- Destroy AWS proof resources and require isolated GCP `No changes.`
- Record acceptance, complete protected CI/review, and merge the focused PR.

## Review First

- `src/dander/bootstrap/project.py`
- `scripts/portability/oci_copy.py`
- `acceptance/cloud-portability/phase1b/smoke/main.tf`
