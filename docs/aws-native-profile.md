# Experimental AWS-native Fargate profile

This runbook covers the named Fargate + Redshift + PostgreSQL state + Glue + AWS Secrets Manager
composition. It is an experimental Phase 8 target, not a supported deployment. Exact RC22 does not
contain the required selected AWS deployment, and its historical Greenhouse objective is not
Redshift-compatible. Private RC26 contains the reviewed runtime-overlay, flat-fixture,
Glue-ownership, materialization, candidate-identity, Fargate ambient-identity, and exact Redshift
staging-role grant corrections at source-free multi-platform index
`sha256:e63aef4b29648864a119219fd973c2a417f5971205907f04997f9009e472d28e`. The exact objective at
`docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives.json` was consumed by one manual
attempt: PostgreSQL setup completed, then Redshift connection validation expired at its 120-second
bound before any warehouse operation. Replay did not start, and exact cleanup completed. Do not
reuse that objective; a fresh committed replacement must reach protected main before another AWS
mutation. RC26 remains private and current while the connection delay is isolated. This is not a
public-release or support claim.

## Ownership and prerequisites

Use one AWS account and region throughout. Dander consumes existing data-plane resources; the
ordinary platform stack does not create Redshift, PostgreSQL, the staging bucket, or the VPC.

- AWS stage zero owns the encrypted/versioned Terraform state bucket, DynamoDB lock table, immutable
  private ECR repository, and a deployment role trusted by one exact operator principal. Two
  separate managed policies give that short-lived role only the action-, name-, and tag-bounded
  authority required by the disposable Phase 8 qualification root; the D7 policy remains separate.
- Later image promotion, plans, applies, and operations use a short-lived profile for that deployment
  role. Do not use static access keys. AWS account root cannot assume the deployment role; choose a
  non-root IAM user or role as the stage-zero `admin-principal-arn`.
- Supply sorted subnet and security-group IDs. Private subnets need NAT or VPC endpoints for ECR,
  CloudWatch Logs, Secrets Manager, S3, and the other AWS APIs used by the task. The task security
  group must reach Redshift on port 5439 and PostgreSQL on port 5432.
- Redshift must be reachable from those subnets and have an IAM COPY role restricted to the declared
  same-region staging bucket prefix. For Serverless, precreate the declared database role with the
  required DDL and COPY permissions; the Fargate task role maps it through `RedshiftDbRoles`.
  PostgreSQL must require TLS and contain no application rows.
- Store the PostgreSQL DSN and every application secret in the selected account and region. The
  manifest contains only full Secrets Manager ARN references, never secret values.

The Phase 8 qualification fixture under `infra/qualification/aws-native` is disposable test
infrastructure, not a production topology or an automatic spending cap. It exports
`network.assign_public_ip: true`; retain that value for this fixture because its public subnets have
an Internet Gateway but no NAT or private AWS service endpoints. The task has no inbound rule, and
its public egress is limited to TLS plus self-scoped database traffic. The root requires exact
`candidate_version` and unique `name` inputs; candidate tags and staging prefixes derive from that
version and cannot inherit RC22 identity.

The qualification pipeline reads three flat synthetic posts from an immutable upstream Git commit.
Its declared schema contains only Redshift-compatible scalar fields, and its one model uses Dander's
portable SQL contract. Do not substitute the nested Greenhouse fixture: that schema intentionally
exercises ARRAY, RECORD, and JSON portability behavior and cannot satisfy this Redshift objective.

## Configure the exact profile

Keep portable pipeline intent in `dander.yaml`:

```yaml
version: 2
pipelines:
  phase8_aws_qualification:
    source: phase8_aws_fixture
    models: [stg_phase8_aws__posts]
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
      database_role: dander_runtime
      copy_role_arn: arn:aws:iam::123456789012:role/DanderRedshiftCopy
      staging_bucket: dander-aws-native-staging
      staging_prefix: dander/staging
      connect_timeout_seconds: 120
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
      assign_public_ip: true
    runtime:
      cpu: 1
      memory: 2Gi
      timeout_seconds: 600
      max_retries: 0
      batch_rows: 1000
    safety:
      require_guarded_free_tier: false
    pipelines:
      phase8_aws_qualification:
        schedule: 0 9 * * *
        time_zone: America/New_York
        paused: true
        secret_bindings:
          DANDER_POSTGRES_DSN: aws-sm://arn:aws:secretsmanager:us-east-1:123456789012:secret:dander/postgres-dsn-AbCdEf
```

The 120-second connection timeout is bound only to this RC26 qualification objective and remains
below the 600-second whole-runtime deadline. It does not change the provider's 30-second default.

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
before continuing. An account created from an older stage-zero root must repeat this saved-plan
upgrade once and accept only the two Phase 8 managed policies plus their deployment-role attachments
before the qualification data-plane plan; do not run that later plan as the administrator.

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
destroy, account/region, wildcard secret, staging prefix, task role, schedule, or image. This
disposable fixture specifically requires `assign_public_ip: true`; reject a disabled task public IP
unless private egress has been separately provisioned and reviewed. Then run only the printed
`dander init-aws-apply` command. A later identical plan must report no changes.

The saved plan carries the validated selected platform as bounded non-secret JSON. At execution,
Dander writes it mode `0600` under task scratch space and removes it on every terminal path. Secret
values are resolved separately with the task role and never enter that overlay or the Fargate plan
and state.

## Operate and verify

Keep the schedule paused. Start one paid manual execution only after reviewing the provider budget:

```bash
dander aws run --deployment aws_fargate --pipeline phase8_aws_qualification --aws-profile dander-deploy
dander aws status --deployment aws_fargate --pipeline phase8_aws_qualification --aws-profile dander-deploy
dander aws logs --deployment aws_fargate --pipeline phase8_aws_qualification \
  --execution-arn EXECUTION_ARN --aws-profile dander-deploy
dander aws replay --deployment aws_fargate --pipeline phase8_aws_qualification \
  --execution-arn TERMINAL_EXECUTION_ARN --aws-profile dander-deploy
dander aws verify --deployment aws_fargate --pipeline phase8_aws_qualification \
  --expected-image 123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:ACCEPTED_INDEX_DIGEST \
  --aws-profile dander-deploy
```

`run`, `cancel`, and `replay` confirm before changing paid execution state. Status, logs, and verify
are read-only and emit normalized records. Require successful manual/replay equality, clean leases
and staging objects, bounded logs, expected Glue readback from
`dander_analytics_staging.stg_phase8_aws__posts`, and a final no-change Terraform plan before
enabling a schedule.

## Upgrade, rollback, and cleanup

For an upgrade, promote the reviewed replacement digest, rerun the same manifest-bound plan, and
accept only the expected task-definition/image changes. Verify manually before re-enabling a paused
schedule. Rollback uses the same process with the last accepted immutable digest; never edit state,
retag an image, or rebuild the rollback artifact.

Destroy launcher resources before disposable data-plane resources. Produce and inspect an exact
saved destroy plan against the same remote state and variables; do not delete a state object as a
substitute for destroy. Confirm the Fargate task definitions/controller/schedules/logs/alerts first,
then the qualification root. That root predeclares and owns the exact Glue database/table that the
runtime updates; its destroy therefore removes Glue together with Redshift, RDS, S3, network, IAM,
and the other disposable resources even if execution was interrupted. Confirm both Terraform state
inventories are empty. Retained stage-zero state, registry, or deployment-role resources are
separate ownership decisions.

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
