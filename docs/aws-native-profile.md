# Experimental AWS-native Fargate profile

This runbook covers the named Fargate + Redshift + PostgreSQL state + Glue + AWS Secrets Manager
composition. It is an experimental Phase 8 target, not a supported deployment. Exact RC22 does not
contain the required selected AWS deployment. Use no candidate until the runtime-overlay correction
has passed protected review and a replacement source-free multi-platform digest is published.

## Ownership and prerequisites

Use one AWS account and region throughout. Dander consumes existing data-plane resources; the
ordinary platform stack does not create Redshift, PostgreSQL, the staging bucket, or the VPC.

- AWS stage zero owns the encrypted/versioned Terraform state bucket, DynamoDB lock table, immutable
  private ECR repository, and a deployment role trusted by one exact operator principal.
- Later image promotion, plans, applies, and operations use a short-lived profile for that deployment
  role. Do not use static access keys. AWS account root cannot assume the deployment role; choose a
  non-root IAM user or role as the stage-zero `admin-principal-arn`.
- Supply sorted subnet and security-group IDs. Private subnets need NAT or VPC endpoints for ECR,
  CloudWatch Logs, Secrets Manager, S3, and the other AWS APIs used by the task. The task security
  group must reach Redshift on port 5439 and PostgreSQL on port 5432.
- Redshift must be reachable from those subnets and have an IAM COPY role restricted to the declared
  same-region staging bucket prefix. PostgreSQL must require TLS and contain no application rows.
- Store the PostgreSQL DSN and every application secret in the selected account and region. The
  manifest contains only full Secrets Manager ARN references, never secret values.

The Phase 8 qualification fixture under `infra/qualification/aws-native` is disposable test
infrastructure, not a production topology or an automatic spending cap.

## Configure the exact profile

Keep portable pipeline intent in `dander.yaml`:

```yaml
version: 2
pipelines:
  greenhouse_jobs:
    source: greenhouse_job_board
    models: [stg_greenhouse__jobs]
    publish_catalog: true
```

Put account-local coordinates in `dander.platforms.yaml`. Replace every example coordinate; keep
the schedule paused through the first manual and replay checks.

```yaml
version: 1
platforms:
  aws_native:
    warehouse:
      provider: redshift
      deployment: serverless
      host: example.123456789012.us-east-1.redshift-serverless.amazonaws.com
      database: analytics
      schema: raw
      region: us-east-1
      workgroup_name: dander-analytics
      copy_role_arn: arn:aws:iam::123456789012:role/DanderRedshiftCopy
      staging_bucket: dander-aws-native-staging
      staging_prefix: dander/staging
    state:
      provider: postgresql
      authority_id: postgresql:aws-native
      dsn_env: DANDER_POSTGRES_DSN
    catalog:
      provider: glue
      region: us-east-1
      catalog_id: "123456789012"
      database_prefix: dander
    secrets:
      provider: aws_secret_manager
      region: us-east-1
deployments:
  aws_fargate:
    platform: aws_native
    launcher:
      provider: fargate
      region: us-east-1
      aws_account_id: "123456789012"
      subnet_ids: [subnet-0123456789abcdef0, subnet-1123456789abcdef0]
      security_group_ids: [sg-0123456789abcdef0]
      architecture: X86_64
      assign_public_ip: false
    runtime:
      cpu: 1
      memory: 2Gi
      timeout_seconds: 900
      max_retries: 1
      batch_rows: 10000
    safety:
      require_guarded_free_tier: false
    pipelines:
      greenhouse_jobs:
        schedule: 0 9 * * *
        time_zone: America/New_York
        paused: true
        secret_bindings:
          DANDER_POSTGRES_DSN: aws-sm://arn:aws:secretsmanager:us-east-1:123456789012:secret:dander/postgres-dsn-AbCdEf
```

Run `dander validate --deployment aws_fargate` before any provider mutation. Project validation
rejects a cross-account or cross-region secret, COPY role, or Glue catalog; `init-aws-plan`
separately rejects an ECR image whose account or region does not match the selected launcher.

## Create AWS stage zero once

Choose an operator-artifact directory outside the project and a non-root trusted principal. The
first command is plan-only:

```bash
export DANDER_AWS_ACCOUNT_ID="123456789012"
export DANDER_AWS_REGION="us-east-1"
export DANDER_AWS_STATE_BUCKET="dander-123456789012-state"
export DANDER_AWS_OPERATOR_DIR="/secure/operator/dander/aws/bootstrap"
export DANDER_AWS_ADMIN_PRINCIPAL="arn:aws:iam::123456789012:user/dander-installer"

aws sts get-caller-identity
dander init-aws-admin-plan \
  --aws-account-id "$DANDER_AWS_ACCOUNT_ID" \
  --region "$DANDER_AWS_REGION" \
  --state-bucket "$DANDER_AWS_STATE_BUCKET" \
  --admin-principal-arn "$DANDER_AWS_ADMIN_PRINCIPAL" \
  --operator-artifact-dir "$DANDER_AWS_OPERATOR_DIR"
```

Inspect the printed saved plan. Apply only that file with the exact printed
`dander init-aws-admin-apply` command. Stage zero is the only step that may use an AWS administrator.
Configure a short-lived local profile such as `dander-deploy` to assume the returned deployment role
before continuing.

## Promote, plan, and apply one candidate

Copy an accepted source-free index without rebuilding it. Both registries must report the same index
and per-platform digests:

```bash
dander image-promote-aws \
  --source-image SOURCE_REGISTRY/dander@sha256:ACCEPTED_INDEX_DIGEST \
  --aws-account-id "$DANDER_AWS_ACCOUNT_ID" \
  --region "$DANDER_AWS_REGION" \
  --aws-profile dander-deploy
```

Use the immutable ECR digest printed by that command:

```bash
dander init-aws-plan \
  --deployment aws_fargate \
  --state-bucket "$DANDER_AWS_STATE_BUCKET" \
  --lock-table dander-terraform-locks \
  --container-image 123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:ACCEPTED_INDEX_DIGEST \
  --aws-profile dander-deploy
```

Review the printed `terraform -chdir=infra/aws show -no-color ...` command. Reject an unexpected
destroy, account/region, public IP, wildcard secret, staging prefix, task role, schedule, or image.
Then run only the printed `dander init-aws-apply` command. A later identical plan must report no
changes.

The saved plan carries the validated selected platform as bounded non-secret JSON. At execution,
Dander writes it mode `0600` under task scratch space and removes it on every terminal path. Secret
values are resolved separately with the task role and never enter that overlay or the Fargate plan
and state.

## Operate and verify

Keep the schedule paused. Start one paid manual execution only after reviewing the provider budget:

```bash
dander aws run --deployment aws_fargate --pipeline greenhouse_jobs --aws-profile dander-deploy
dander aws status --deployment aws_fargate --pipeline greenhouse_jobs --aws-profile dander-deploy
dander aws logs --deployment aws_fargate --pipeline greenhouse_jobs \
  --execution-arn EXECUTION_ARN --aws-profile dander-deploy
dander aws replay --deployment aws_fargate --pipeline greenhouse_jobs \
  --execution-arn TERMINAL_EXECUTION_ARN --aws-profile dander-deploy
dander aws verify --deployment aws_fargate --pipeline greenhouse_jobs \
  --expected-image 123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:ACCEPTED_INDEX_DIGEST \
  --aws-profile dander-deploy
```

`run`, `cancel`, and `replay` confirm before changing paid execution state. Status, logs, and verify
are read-only and emit normalized records. Require successful manual/replay equality, clean leases
and staging objects, bounded logs, expected Glue readback, and a final no-change Terraform plan
before enabling a schedule.

## Upgrade, rollback, and cleanup

For an upgrade, promote the reviewed replacement digest, rerun the same manifest-bound plan, and
accept only the expected task-definition/image changes. Verify manually before re-enabling a paused
schedule. Rollback uses the same process with the last accepted immutable digest; never edit state,
retag an image, or rebuild the rollback artifact.

Destroy launcher resources before disposable data-plane resources. Produce and inspect an exact
saved destroy plan against the same remote state and variables; do not delete a state object as a
substitute for destroy. Confirm the Fargate task definitions/controller/schedules/logs/alerts first,
then Redshift/RDS/Glue/S3/network/IAM resources and both state inventories. Retained stage-zero state,
registry, or deployment-role resources are separate ownership decisions.

## Troubleshooting

| Symptom | Check first |
|---|---|
| Deployment role cannot be assumed | The caller is the exact non-root trusted principal; AWS account root cannot assume an IAM role. |
| Plan says the AWS deployment is missing | The candidate predates the runtime-overlay correction; do not work around it by baking account coordinates into the image. |
| Configuration fails before planning | Account, region, COPY-role partition, Glue catalog, full secret ARNs, sorted network IDs, and ECR digest must all match. |
| Task cannot pull or emit logs | Private-subnet NAT/endpoints, task execution role, ECR, and CloudWatch Logs reachability. |
| Secret resolution is denied | The full ARN is in the selected account/region and the task role is scoped to that exact secret. Do not log or inline its value. |
| Redshift COPY fails | Workgroup/cluster reachability, same-region staging prefix, COPY-role trust, manifest objects, and the task role's prefix access. |
| PostgreSQL times out | Port 5432 security-group path, DNS, TLS certificate, DSN secret, and pool timeout. |
| Nothing runs on schedule | `paused` is still true, or EventBridge delivery/controller status reports a bounded failure. |

Do not mark the profile supported from a successful start alone. Phase 8 still requires exact-candidate
correctness, scale/cost, replay, failure, cleanup, no-drift, scheduled soak, and protected audit
evidence.
