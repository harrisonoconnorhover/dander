---
id: DANDER-120
title: Add the GraphStore contract and local adapters
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-119]
created: 2026-08-13
---

## Context

Hosted multi-graph routing must not embed a second one-file persistence implementation.

## Acceptance Criteria

- [ ] Define list/get/create/put/delete semantics with strict project/graph identifiers, bounded
      pagination/documents, opaque revisions, separate canonical content hashes, and idempotency.
- [ ] Implement in-memory and rooted local-filesystem adapters without arbitrary path input.
- [ ] Preserve canonical serialization and conditional stale-write/delete rejection.
- [ ] One conformance suite proves create/read/list/update-conflict/restart/delete behavior.
- [ ] Existing `dander graph serve --file` behavior and tests remain unchanged.

## Design

Land the port before or with hosted routing. Provider generations and ETags remain adapter-private
opaque revision material; cross-cloud evidence compares canonical content SHA-256 only.

## Implementation Notes

_Pending._

## Review Log

_Pending._
