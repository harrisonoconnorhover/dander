# Cloud Portability Phase 1B Acceptance

Accepted on 2026-08-09 against public `dander-platform==0.8.0rc1`, published from commit
`5614f3479f4535d5ab3acfaa8b579e23cce403fe`. This record is feasibility evidence for one
Fargate-to-BigQuery composition; it does not promote Fargate or any non-GCP profile to supported.

## Artifact identity

- A clean installation outside the repository generated a source-free project with no `src/`
  directory and a Dockerfile pinned to `0.8.0rc1`.
- One Buildx invocation published a Linux/AMD64 and Linux/ARM64 OCI index to staging GAR with SBOM
  and provenance attestations. The index digest was
  `sha256:35bfa11cd0ea51cc9c562b6c76567e2bade56114bfb4ff6768e9c5d4a052bfad`.
- The runnable manifests were AMD64
  `sha256:b5ed7f229a7e8c75a717bdd88b64ee11e7c37d77675ad23e2e79a9a29f6a0311` and ARM64
  `sha256:d4b46e03d286a03a59524ef50f333999987c84d041dea0245f9f67db3805fecc`.
- `dander image-promote-aws` copied the accepted index to ECR without rebuilding it. Independent
  `crane` inspection confirmed the same index and both runnable manifests after the copy.

## Live execution

- A reviewed Terraform plan contained exactly 18 creates, zero changes, and zero destroys. It
  created one disposable network, ECS cluster/task definition, separate task and execution roles,
  log group, Google service account, AWS Workload Identity provider, and the minimum BigQuery IAM.
- The first apply attempt exposed a local credential-selection error: Terraform used an older ADC
  identity instead of the active project owner. It created 11 AWS shell resources and no GCP
  identity. Those 11 resources were removed through a reviewed destroy plan before the accepted
  plan was recreated with the owner's short-lived access token.
- The accepted task definition selected ARM64, the immutable ECR index, a read-only root
  filesystem, and no task-definition secrets. The task role had zero attached and zero inline AWS
  policies; only the distinct execution role could pull the image and write logs.
- Exactly one Fargate task ran. It exited `0`, reported `aarch64`, and counted 17 rows in the
  disposable `raw.salesforce_accounts` table twice. Its Google credential expiry advanced from
  `2026-08-09T18:30:13Z` to `2026-08-09T18:40:28Z`, followed by
  `credential.refresh_observed`.
- One separately planned Cloud Run conformance job executed the same GAR index. Cloud Run selected
  the recorded AMD64 manifest, reported `x86_64`, completed runtime conformance, and exited `0`.

## Security and cleanup

- The generated external-account configuration, Dockerfile, artifact records, Terraform state,
  task description, and CloudWatch events contained no long-lived AWS or Google credential.
- Both extracted platform filesystems passed the credential scan after recognizing three exact
  SHA-256-pinned public boto3/botocore example files. Any content change removes that exception and
  restores normal detection; no credential value is allowlisted.
- Docker Scout reported zero application-layer and zero fixable high/critical findings on both
  architectures. It reported two critical and two high, unfixed `perl-base` advisories inherited
  from `python:3.12-slim`; no fixed base image was available during acceptance.
- Reviewed destroy plans removed all 18 smoke resources, the one Cloud Run conformance job, and
  both ECR resources. The proof GAR tag and digest were deleted. ECS reports the expected inactive
  cluster tombstone with zero tasks/services, and the disposable Google pool is soft-deleted.
- The pre-existing isolated GCP platform finished with exact `No changes.` using its deployed
  stable Dander `0.7.0` configuration and image. The retained Dander project was not touched.

## Result

Phase 1B passed: one source-free multi-platform image retained identical registry content, ran on
Cloud Run and Fargate, and reached BigQuery from AWS before and after keyless credential refresh.
The later [Fargate lifecycle acceptance](cloud-portability-fargate-lifecycle-acceptance.md) passed
correctness and operating gates. Fargate remains experimental pending the published scale and
profile-qualification objectives.
