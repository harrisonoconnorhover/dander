# Dander AWS platform stack

This separate Terraform root projects validated `io.dander.execution/v1` templates onto ECS
Fargate. It creates an immutable ECR repository, ECS task definitions, distinct execution/task
roles, a Standard Step Functions controller, paused-aware EventBridge schedules, CloudWatch logs,
and an encrypted failure queue plus notification topic.

This root is construction-ready but is not yet selected by the public Dander plan/apply commands.
Initialize it with an operator-owned encrypted S3 backend and review a saved plan before any apply.
The stack accepts existing VPC subnet and security-group IDs; it does not create a network.

The controller uses `ecs:runTask.sync`. One absolute Step Functions deadline bounds all attempts;
AWS performs a best-effort `StopTask` when the integration is cancelled or times out. Exit code 75
is the only runtime outcome eligible for a bounded launcher retry. Scheduler delivery retries use a
separate counter and queue.

No AWS deployment is supported until source-free live acceptance proves manual and scheduled runs,
deadline cancellation, replay, identity refresh, alerts, rollback, and no-change reconciliation.
