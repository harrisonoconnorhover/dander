---
id: DANDER-236
title: Select AWS or GCP execution through one Control
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-235]
created: 2026-08-26
---

## Context

DANDER-235 exercised the AWS-hosted Control path and left its reusable defects repaired on exact
main. The next smallest vertical slice must let that same always-on Control select the existing
Cloud Run/BigQuery runtime without changing pipeline logic, the durable lifecycle, or the single
Dander worker container.

## Acceptance Criteria

- [x] Select an immutable plan by optional API environment while preserving the configured default.
- [x] Keep scheduled wakeups on their existing exact-plan-revision path.
- [x] Register Fargate and Cloud Run backends in one provider-neutral lifecycle.
- [x] Dispatch or adopt one deterministic Cloud Run execution and normalize status, results,
  logs, cancellation, and cleanup through existing Control contracts.
- [x] Verify the deployed Cloud Run Job still matches the canonical image, command, identity, and
  bounded task settings before starting it.
- [x] Let the AWS Control task federate keylessly into a narrowly scoped GCP service account.
- [x] Preserve provider-native worker identity, secret/config handoff, direct CLI execution, and
  the single-container runtime.
- [x] Cover restart adoption, lost responses, drift, terminal outcomes, logs, cancellation,
  mixed-backend composition, API compatibility, rendering, packaging, and Terraform.

## Design

API callers may add `environment=gcp`; omitted selection retains the configured default. Schedules
already name one plan revision, so they need no new provider field. The plan registry chooses the
backend, while the existing lifecycle continues to own durable idempotency, attempts, retries,
recovery, cancellation, and result/cleanup truth.

Cloud Run receives a deterministic `startExecutionToken` derived from the durable run and attempt.
Before setting it, Control reads the existing Job and verifies its immutable plan fields. AWS ECS
task credentials exchange through Google Workload Identity Federation into one Control service
account; the Job's existing runtime service account and secret configuration remain unchanged.

## Boundaries

- No Spark, Kubernetes, dynamic cluster sizing, generalized autoscaling, GCP-hosted Control,
  Pub/Sub schedule path, GCS run store, or release is added.
- Live combined acceptance follows protected merge from one exact-main immutable image and one
  disposable environment; operator evidence is not committed or submitted as a status-only PR.

## Review Log

_Awaiting protected PR review._
