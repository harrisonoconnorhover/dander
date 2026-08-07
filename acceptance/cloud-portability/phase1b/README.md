# Phase 1B — Artifact Copy and Cross-Cloud Identity Proof

This bounded acceptance proves two assumptions before Dander adds provider factories: one
multi-platform OCI index can move from GAR to ECR without a rebuild, and an isolated Fargate task
can query BigQuery before and after a short-lived Google credential refresh without either cloud's
static access keys.

This directory is proof infrastructure, not a supported Fargate launcher. It creates no ECS
service or schedule. Use only disposable accounts/projects, saved Terraform plans, and an operator
artifact directory outside the repository. Never commit generated credential configuration,
Terraform state, task descriptions, or logs.

## Prerequisites

- Dander `0.7.0`, Docker Buildx, Terraform 1.9+, `gcloud`, and AWS CLI authenticated with
  short-lived browser/SSO credentials.
- `crane` pinned to `v0.20.6` (`go install
  github.com/google/go-containerregistry/cmd/crane@v0.20.6`).
- A disposable billing-linked GCP project containing the bounded proof table.
- A disposable AWS account. Do not create an AWS access key.

The tested GCP target is project `project-092b24a8-26a3-4438-8cd`, dataset `dander_raw`, table
`greenhouse_job_board_jobs`. The retained Dander project is out of scope.

## Reviewed sequence

1. Initialize `ecr/`, save and review its plan, then apply only that saved plan. Record
   `repository_url`.
2. Generate a fresh project from the public package into an operator temp directory. Confirm it
   has no `src/` directory.
3. Generate an AWS external-account file for the deterministic provider name and probe service
   account. This command writes configuration, not a secret:

   ```console
   gcloud iam workload-identity-pools create-cred-config \
     projects/1009770943166/locations/global/workloadIdentityPools/dander-phase1b-aws/providers/fargate \
     --service-account=dander-phase1b-aws@project-092b24a8-26a3-4438-8cd.iam.gserviceaccount.com \
     --aws --output-file="$OPERATOR_DIR/external-account.json"
   ```

4. Prepare only the generated context. The helper validates `external_account`/`aws1`, rejects
   private keys and client secrets, and requests a 600-second impersonated token:

   ```console
   python scripts/portability/prepare_phase1b_context.py \
     --project-dir "$PROJECT_DIR" \
     --credential-config "$OPERATOR_DIR/external-account.json"
   ```

5. From the generated project, use this branch's `dander image-publish` to build one
   `linux/amd64,linux/arm64` index in staging GAR. Preserve `.dander/runtime-artifact.json` outside
   the repository.
6. Authenticate Docker to GAR and ECR with short-lived tokens. Copy and verify without rebuilding:

   ```console
   python scripts/portability/oci_copy.py \
     --source "$GAR_IMAGE_AT_DIGEST" \
     --destination "$ECR_REPOSITORY:phase1b-$SHORT_REVISION" \
     --record "$OPERATOR_DIR/oci-copy.json"
   ```

   The command fails unless the index digest and both runnable platform-manifest digests are
   identical after the copy.
7. Set the verified ECR digest in `smoke/terraform.tfvars`, initialize `smoke/`, save and review its
   plan, then apply only that saved plan. The execution role can pull/log; the separate task role
   has no AWS permission policy and is the only role trusted by Google WIF.
8. Run exactly one Fargate task using the Terraform outputs for cluster, task definition, subnet,
   and security group. Wait for terminal status and save `describe-tasks` plus the CloudWatch log
   stream outside the repository.
9. Require two `query.completed` events followed by `credential.refresh_observed`. The second
   expiry must be later than the first. No event may contain a token, URL, query body, or record.
10. Run `scan_long_lived_credentials.py` over the generated config, both Terraform states, task
    definition/description, exported logs, and an extracted image filesystem. Run the normal image
    vulnerability and secret scans as well.
11. Run the same GAR index in the isolated Cloud Run proof job and confirm its selected `amd64`
    manifest matches the recorded index. Restore the isolated project through its reviewed
    manifest-aware plan and require `No changes.`
12. Destroy the smoke stack and ECR root from reviewed destroy plans. If teardown is deferred, the
    account must still contain no ECS service or schedule.

## Pass criteria

- One build; identical GAR/ECR index and `amd64`/`arm64` digests; no registry rewrite.
- The Fargate task uses the task role while ECS pull/logging uses the execution role.
- BigQuery succeeds before and after observed Google credential expiry/refresh.
- No Google service-account key, AWS access key, or secret value appears in inputs, state, task
  definition, logs, or image.
- Both proof roots are destroyed and the isolated GCP platform finishes with no Terraform drift.
