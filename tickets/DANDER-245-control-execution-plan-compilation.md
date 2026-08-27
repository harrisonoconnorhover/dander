---
id: DANDER-245
title: Compile and render immutable hosted execution plans
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-244]
created: 2026-08-27
---

## Context

DANDER-244 compiled canonical graphs into bounded physical plans, but operator code still had to
assemble each immutable `ExecutionPlan`. This slice makes deployment-time plan assembly a pure
Control capability and passes its canonical output through the existing hosted-Control renderer.

## Acceptance Criteria

- [x] Derive graph identity from `GraphRecord` and backend/runtime identity from the existing
  `ExecutionTemplate`.
- [x] Produce fused Fargate and distributed Dataproc plans for the same canonical graph.
- [x] Remove schedule expression and time zone from provider templates while preserving all other
  template intent; `TriggerSpec` remains the schedule owner.
- [x] Emit deterministic revision-sorted canonical JSON through the existing AWS Control
  `execution_plan_json` input and rendered plan-file arguments.
- [x] Reload the rendered plans with verified revisions for restart recovery.
- [x] Preserve the existing single-container command except for its final canonical physical-plan
  binding.

## Boundaries

- No provider calls, image publication, cloud qualification, Terraform/schema changes, runtime
  graph compilation, or API/scheduler behavior changes.
- No reusable Spark operators, joins, dynamic topology or sizing, autoscaling, Kubernetes, or new
  reconciler.
