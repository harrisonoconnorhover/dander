# Dander AWS platform stack

This separate Terraform root projects validated `io.dander.execution/v1` templates onto ECS
Fargate. It consumes the immutable ECR repository created by AWS stage zero, then creates ECS task
definitions, distinct execution/task roles, a Standard Step Functions controller, paused-aware
EventBridge schedules, CloudWatch logs, and an encrypted failure queue plus notification topic.

`dander init-aws-plan` selects one version-2 Fargate deployment, renders its complete execution
projections, and saves a Terraform plan in this root. It requires an existing encrypted S3 state
bucket, a DynamoDB lock table, and an immutable image digest reference for the selected account and
region. The command never applies. After reviewing the saved plan, an operator may use
`dander init-aws-apply` to apply only that plan.

```console
dander init-aws-plan \
  --project YOUR_GCP_DATA_PROJECT \
  --deployment aws_fargate \
  --state-bucket YOUR_AWS_STATE_BUCKET \
  --lock-table YOUR_TERRAFORM_LOCK_TABLE \
  --container-image 123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:DIGEST
```

For the named AWS-native Redshift/PostgreSQL/Glue/AWS-Secrets profile, omit `--project`. The
validated manifest supplies the existing Redshift, PostgreSQL-state, Glue, and full Secrets
Manager coordinates. The saved plan scopes each task role to those declared resources and never
accepts a static AWS credential.

Run AWS stage zero and promote the accepted source-free image before planning this root. The stack
accepts existing VPC subnet and security-group IDs; it does not create a network. Use the dedicated
stage-zero deployment role for planning and application rather than the account root identity.
The stack also consumes, but does not create, the AWS-native Redshift, PostgreSQL, staging-bucket,
Glue, and application-secret resources.

The controller uses `ecs:runTask.sync`. One absolute Step Functions deadline bounds all attempts;
AWS performs a best-effort `StopTask` when the integration is cancelled or times out. Exit code 75
is the only runtime outcome eligible for a bounded launcher retry. Scheduler delivery retries use a
separate counter and queue.

After a reviewed platform apply, operate one exact manifest-bound pipeline with the dedicated
short-lived deployment-role profile:

```console
dander aws run --deployment aws_fargate --pipeline greenhouse_jobs --aws-profile dander-deploy
dander aws status --deployment aws_fargate --pipeline greenhouse_jobs --aws-profile dander-deploy
dander aws logs --deployment aws_fargate --pipeline greenhouse_jobs \
  --execution-arn EXECUTION_ARN --aws-profile dander-deploy
dander aws replay --deployment aws_fargate --pipeline greenhouse_jobs \
  --execution-arn TERMINAL_EXECUTION_ARN --aws-profile dander-deploy
dander aws verify --deployment aws_fargate --pipeline greenhouse_jobs \
  --expected-image 123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:DIGEST \
  --aws-profile dander-deploy
```

`run`, `cancel`, and `replay` require confirmation because they mutate paid AWS execution state.
Status, logs, and verification are read-only. Commands validate that supplied execution ARNs belong
to the selected pipeline and print only Dander's normalized operation records.

No AWS deployment is supported until source-free live acceptance proves manual and scheduled runs,
deadline cancellation, replay, identity refresh, alerts, rollback, and no-change reconciliation.
The complete experimental operator sequence, configuration example, network boundary, upgrade,
rollback, cleanup, and troubleshooting checks are in `docs/aws-native-profile.md`.
