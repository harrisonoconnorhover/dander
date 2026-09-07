---
id: DANDER-257
title: Select Control durability and schedules through typed startup bindings
status: done
component: python
epic: hdfs-enterprise
depends_on: [DANDER-256]
created: 2026-08-30
---

## Acceptance Criteria

- [x] Add closed non-secret S3/PostgreSQL run-store and SQS/PostgreSQL schedule-source bindings.
- [x] Start PostgreSQL Control without AWS credentials, APIs, queues, or object stores.
- [x] Keep legacy AWS flags as mutually exclusive compatibility wrappers for one minor release.
- [x] Migrate before startup and close one shared PostgreSQL pool after lifecycle shutdown.
- [x] Preserve existing Fargate, Cloud Run, and Dataproc startup behavior.

## Boundaries

- No backend expansion, Kubernetes HA, or support promotion.
