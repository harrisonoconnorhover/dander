# Fargate launcher module

This module consumes only validated execution projections. It deliberately keeps runtime task
roles separate from the shared image-pull/log execution role. AWS Secrets Manager access, when
declared, is attached only to the matching task role and exact secret ARNs. GCP secret references
remain resource names resolved through keyless Google federation inside the task.

Schedules use EventBridge Scheduler's universal Step Functions `StartExecution` target so delivery
and launcher retry counters remain separate. The state machine is Standard, applies one absolute
deadline to every whole-runtime attempt, retries only Dander exit code 75, and emits safe exhausted
failure records to SQS. The AWS-managed ECS `.sync` integration owns task observation and performs
a best-effort `StopTask` on timeout or cancellation.
