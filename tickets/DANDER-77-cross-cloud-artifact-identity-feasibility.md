# DANDER-77 — Cross-cloud artifact and identity feasibility

Status: in progress

## Outcome

Prove the roadmap's Phase 1B gate before broad provider factories are introduced: publish one
source-free multi-platform OCI index, copy it from GAR to ECR without rebuilding, and run a manual
Fargate BigQuery probe across a real Google credential expiry/refresh using only workload identity.

## Acceptance criteria

- [x] `image-publish` builds `linux/amd64` and `linux/arm64` and rejects an incomplete index.
- [x] Digest-copy tooling fails on source mismatch, destination index rewrite, or platform drift.
- [x] The source-free proof context accepts only AWS external-account configuration and bounded
  impersonated-token lifetime.
- [x] Isolated Terraform separates ECS execution and task roles, creates no service or schedule,
  grants the Google identity only BigQuery job and one-dataset read access, and validates locally.
- [x] Probe and scanner tests cover credential refresh, bounded waits, sanitized output, and
  recognizable long-lived cloud credentials.
- [x] One source-free OCI index was built in staging GAR; its recorded `amd64` and `arm64`
  manifests both passed the read-only local runtime conformance command.
- [ ] The index and both platform manifests are identical in staging GAR and ECR.
- [ ] Cloud Run selects the recorded `amd64` content and the Fargate task selects a recorded
  platform from the same index.
- [ ] A live Fargate task reads BigQuery before and after Google credential refresh.
- [ ] State, task definition, logs, configuration, and image scans find no static cloud key.
- [ ] Both AWS stacks are destroyed, the isolated GCP configuration is restored, and its final
  Terraform plan reports `No changes.`

## Boundaries

- This is a feasibility proof, not an ECS/Fargate support claim.
- No retained-project mutation, background service, schedule, static key, or provider factory.
- Live evidence is recorded only after the new AWS account is authenticated with short-lived
  credentials and every paid apply has a saved, reviewed plan.
