# DANDER-77 — Cross-cloud artifact and identity feasibility

Status: complete

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
- [x] The index and both platform manifests are identical in staging GAR and ECR.
- [x] Cloud Run selects the recorded `amd64` content and the Fargate task selects a recorded
  platform from the same index.
- [x] A live Fargate task reads BigQuery before and after Google credential refresh.
- [x] State, task definition, logs, configuration, and image scans find no static cloud key.
- [x] Both AWS stacks are destroyed, the isolated GCP configuration is restored, and its final
  Terraform plan reports `No changes.`

## Live evidence

On 2026-08-07, the accepted source-free GAR index was
`sha256:5e026166092ec79d22562781596bc40d7f2e64f89030ac9126000d4614880c82`, with runnable manifests
`sha256:5eb1e1618378c8a9f1671e6746531976cf3225bdf1d8955fa0a4c63e09b192b7` (`linux/amd64`) and
`sha256:4488eecc4fd888379f741553302ef1688a8a3c6537f8b1f59f1c063b3e799fd0` (`linux/arm64`). Both
passed local conformance. Cloud Run execution `dander-phase1b-conformance-b4v57` selected the
recorded AMD64 manifest, reported `x86_64`, completed `io.dander.runtime/v1`, and exited zero. The
temporary unscheduled job was deleted and both managed schedules remained paused.

The exact index and both runnable manifests copied to private ECR without a registry rewrite.
Fargate task `e3840b6301dd4cce8af5ebce6f7cee40` selected ARM64, reported `aarch64` and
`ecs_task_role`, counted 17 disposable Salesforce Account rows, refreshed its Google credential
from `2026-08-07T19:30:36Z` to `2026-08-07T19:40:51Z`, counted the same 17 rows again, and exited
zero. Its task role had no attached or inline AWS policies; the execution role was limited to the
standard ECS pull/log policy.

The first live attempts were rejected rather than blessed: the original proof target did not
exist, Google Auth initially tried EC2 metadata instead of Fargate's ECS credential endpoint, and
an ignored impersonation-options spelling produced a 3,600-second token outside the bounded proof
window. Each defect was corrected before building the final candidate. The scanner found no
recognizable long-lived cloud key across generated configuration, Terraform state, task evidence,
logs, or either image filesystem. Reviewed teardown destroyed all 18 smoke resources and both ECR
resources; AWS has no proof cluster, active task definition, log group, role, or repository, and
the disposable Google WIF pool is `DELETED` with its IAM grants removed. The final isolated
manifest-aware Terraform plan reported exactly `No changes.` The retained project was untouched.

## Boundaries

- This is a feasibility proof, not an ECS/Fargate support claim.
- No retained-project mutation, background service, schedule, static key, or provider factory.
- Live evidence is recorded only after the new AWS account is authenticated with short-lived
  credentials and every paid apply has a saved, reviewed plan.
