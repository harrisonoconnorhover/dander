# Morning Handoff

## Finished

- Made the disposable Phase 1B smoke proof name configurable while preserving its existing default.
- Derived AWS, CloudWatch, Google Workload Identity, and service-account names from one input.
- Documented safe reruns during Google's soft-delete retention window.

## Try It

Set `proof_name = "dander-phase1b-r2"` in the smoke root's reviewed inputs and use the same value
when generating the external-account credential configuration.

## Checks

- Default and `dander-phase1b-r2` smoke plans each produced 18 creates; default resource names were
  unchanged, custom names were isolated, and invalid names failed before planning.
- Ruff/format and strict mypy passed; 1,110 tests passed against PostgreSQL 15; dependency audit
  found no known vulnerabilities.
- Wheel/sdist inspection, source-free installs, runtime-all installation, generated Terraform,
  GCP/AWS Terraform, Helm, and non-root/read-only container conformance passed.
- Retained stage-zero and platform plans each reported exactly `No changes.`; no apply ran.

## Decisions

- Keep `dander-phase1b` as the compatibility default.
- Use a new deterministic name rather than automatically undeleting or adopting prior resources.

## Remaining

- Let protected CI repeat Linux security and container scans, then merge if clean.
- Correct the unrelated stage-zero permission preflight's bucket-level check in a separate PR.
- Regenerate the live proof plans from merged `main`.
- Apply only after explicit paid-action approval.

## Review First

- `acceptance/cloud-portability/phase1b/smoke/variables.tf`
- `acceptance/cloud-portability/phase1b/smoke/main.tf`
- `acceptance/cloud-portability/phase1b/README.md`
