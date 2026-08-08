# Dander AWS platform stack

This separate Terraform root projects validated `io.dander.execution/v1` templates onto ECS
Fargate. It creates an immutable ECR repository, ECS task definitions, distinct execution/task
roles, a Standard Step Functions controller, paused-aware EventBridge schedules, CloudWatch logs,
and an encrypted failure queue plus notification topic.

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

The state bucket, lock table, and usable ECR image lifecycle are separate prerequisites until the
AWS stage-zero and image-publication commands ship. Do not apply this root before those commands
establish consistent repository ownership and publish the referenced digest. The stack accepts
existing VPC subnet and security-group IDs; it does not create a network. Use a least-privilege AWS
role for planning and application rather than the account root identity.

The controller uses `ecs:runTask.sync`. One absolute Step Functions deadline bounds all attempts;
AWS performs a best-effort `StopTask` when the integration is cancelled or times out. Exit code 75
is the only runtime outcome eligible for a bounded launcher retry. Scheduler delivery retries use a
separate counter and queue.

No AWS deployment is supported until source-free live acceptance proves manual and scheduled runs,
deadline cancellation, replay, identity refresh, alerts, rollback, and no-change reconciliation.
