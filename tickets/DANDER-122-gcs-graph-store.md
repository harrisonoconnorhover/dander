---
id: DANDER-122
title: Add GCS GraphStore
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Provide the first object-store implementation without leaking GCS semantics through the API.

## Acceptance Criteria

- [ ] Use generation-match create/update/delete controls and bounded list pagination.
- [ ] Pass the shared mock conformance and one separately approved live restart/conflict/cleanup proof.
- [ ] Verify bucket binding, encryption/versioning policy, cleanup, and no drift without credentials,
      graph rows, plan, or state in evidence.

## Design

Provider coordinates and SDK types stay inside the adapter.

## Implementation Notes

_Pending._

## Review Log

_Pending._
