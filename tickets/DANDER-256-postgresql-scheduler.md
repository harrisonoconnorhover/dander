---
id: DANDER-256
title: Schedule canonical Control wakeups through PostgreSQL
status: done
component: python
epic: hdfs-enterprise
depends_on: [DANDER-255]
created: 2026-08-30
---

## Acceptance Criteria

- [x] Accept bounded five-field cron expressions and IANA time zones at the PostgreSQL boundary.
- [x] Elect one producer with a session advisory lock and transactionally advance durable cursors.
- [x] Deduplicate canonical wakeups and provide leased at-least-once queue delivery.
- [x] Preserve one logical run and provider effect across crash-before-delete replay.
- [x] Pass restart, failover, DST, redelivery, dead-letter, and live PostgreSQL tests.

## Boundaries

- Reuse canonical `ScheduleWakeup` and the existing consumer/lifecycle; no general broker or new
  scheduling API.
