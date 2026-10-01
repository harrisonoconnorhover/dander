---
id: DANDER-243
title: Publish and qualify one immutable Spark driver and image pair
status: done
component: python
epic: control-orchestration
depends_on: [DANDER-242]
created: 2026-08-27
---

## Context

DANDER-242 added the fixed-size Managed Spark backend but intentionally stopped before claiming a
particular driver and image. This slice supplies exactly one narrow pair and one bounded live proof.

## Acceptance Criteria

- [x] Keep the normal Dander image and single-container Fargate/Cloud Run paths unchanged.
- [x] Add a Python 3.11-compatible content-addressed driver for one fixed two-stage physical plan.
- [x] Materialize the stage exchange in GCS, publish through Spark's BigQuery connector, and read
  back deterministic aggregates before emitting the existing canonical runtime completion event.
- [x] Require the submitted driver to be byte-identical to the copy embedded in the immutable image.
- [x] Build an amd64 Debian 12 custom image with Spark's required UID/GID and utilities, without
  bundling Spark or a JRE.
- [x] Contract-test the plan, Control handoff, driver/image identity, and Control result parser.
- [x] Publish one tag-immutable image whose tag resolves to the recorded digest and one same-region
  GCS driver object from exact main.
- [x] Run one two-executor Managed Spark/BigQuery qualification through Control and clean up its
  disposable dataset, bucket contents, batch metadata where supported, and temporary IAM.

## Boundaries

- No dynamic planning, dynamic allocation, autotuning, generalized sizing, Kubernetes, Spark
  cluster creation, or reusable arbitrary-operator runtime is introduced.
- This qualifies one fixed driver/image pair, not every distributed Dander pipeline.
- C27, RC32, Phase 8, releases, and the existing main runtime image remain untouched.

## Retained acceptance reconciliation — September 30, 2026

The August 27–28 operator records confirm the immutable publication and bounded execution
criteria above. See the [reviewed execution history](../docs/control-execution-history.md) for
the exact source revision, result, cleanup boundary, and retained record identifier. Earlier
failed attempts remain historical evidence; no new cloud execution was performed for this update.
