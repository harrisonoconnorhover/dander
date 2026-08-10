# Fargate lifecycle acceptance

Accepted on 2026-08-10 against public `dander-platform==0.8.0rc8`, published from protected-main
merge `819a61c0334b4182178f5951361e68dbb81afb39`. The proof used a generated source-free project,
the disposable GCP project `project-092b24a8-26a3-4438-8cd`, and AWS account `184463061564`.
The retained project was not changed.

## Artifact and deployment

- Public rc8 installed outside the repository and generated a project with no copied `src/`.
- One source-free multi-platform image retained byte-identical GAR and ECR content. Its OCI index
  digest was `sha256:bb69895befa31a384b8eff0e8f783b795a311d924456a5446d01bc8d200369b9`;
  both registries retained AMD64 and ARM64 manifests, SBOM, and provenance attestations.
- Reviewed plans changed only the candidate image, Fargate task-definition revisions, and the
  derived controller/scheduler references. Both schedules remained disabled except during the
  bounded scheduled-execution proof.
- The deployed task definitions selected ARM64, the immutable ECR digest, distinct execution and
  runtime roles, and the accepted non-root scratch-storage contract.

## Execution, replay, and scheduling

- A manual Greenhouse run and its replay each ingested 19 rows, built
  `stg_greenhouse__jobs`, passed three assertions, and published one metadata asset. Raw data
  retained 19 distinct IDs, and the inclusive watermark never regressed.
- EventBridge Scheduler started execution
  `c9252fa8-24ae-4542-acf4-b16189aab872`. AWS substituted the scheduled timestamp, attempt, and
  execution identifier into the Step Functions input; the ECS task used the exact rc8 digest and
  exited `0`.
- The scheduled run recorded a truthful successful ledger row with 19 extracted and 19 affected
  rows, one model, three assertions, and one asset. The Greenhouse lease released and no
  run-scoped staging object remained.
- Both schedules were restored to their tracked daily expressions in `DISABLED` state immediately
  after the bounded proof.

## Interruption and alerts

- A 65-endpoint credential-refresh probe exceeded the 900-second controller deadline. The task
  exited `130`, Step Functions recorded `TIMED_OUT`, run history retained sanitized
  `interrupted_run`, the lease released, and no staging residue required repair.
- A separately reviewed temporary deadline increase allowed the same 65-endpoint probe to finish;
  its 65 checkpoint tables and rows were present, and the configured 900-second deadline was then
  restored.
- The enabled, state-machine-scoped EventBridge failure rule invoked both the KMS-encrypted SNS
  topic and the AWS-managed-SSE SQS queue. The controlled timeout produced observable rule,
  invocation, publish, and queue-delivery metrics.
- The SNS topic deliberately has no human subscription in this disposable proof. Alert routing is
  live-qualified; delivery to an operator is not claimed.

## Rollback and recovery

- The first proposed prior digest,
  `sha256:a05dfe7fcdc50a3003f92b25d0aaa30f2e2ccc7d2e2186d395530a4cc7f3768c`, failed immediately
  with the sanitized code `authentication_failed`. It predates the accepted renewable
  AWS-to-Google identity path and is recorded as incompatible, not as a successful rollback
  target.
- The actual prior working runtime was rc7 digest
  `sha256:15ddaa778c8e5fc682fd1bff6845f2765824be15352bb103652c6298290ae6cb`.
  With schedules paused, a reviewed image-only rollback ran Greenhouse successfully: 19 rows, one
  model, three assertions, monotonic watermark, released lease, and no staging residue.
- A reviewed plan restored rc8. The restored-digest smoke run repeated the same successful data,
  transform, test, lease, cursor, and cleanup checks.

## Final state and result

- No ECS task remains active. The one temporary diagnostic ECR tag was removed; the rc7 rollback
  and accepted rc8 images remain.
- The AWS platform, GCP parity/WIF proof root, retained-project stage zero, and retained-project
  platform each finished with exactly `No changes.`
- Existing failure-queue messages were preserved as operating evidence. No secret, source record,
  unrestricted exception, Terraform state, or alert address is retained here.

The complete Fargate lifecycle gate passes for the named Fargate-to-BigQuery/GCP composition.
Fargate remains experimental until the profile also satisfies the published scale/qualification
objectives; this record does not promote other AWS, warehouse, or cross-cloud combinations.
