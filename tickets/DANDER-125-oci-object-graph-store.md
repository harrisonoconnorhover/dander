---
id: DANDER-125
title: Add OCI Object Storage GraphStore
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Add OCI Object Storage behind the accepted GraphStore semantics.

## Acceptance Criteria

- [ ] Use provider-native conditional/version controls and bounded list pagination.
- [ ] Pass shared mock conformance and a separately approved live restart/conflict/cleanup proof.
- [ ] Use resource principal identity and keep OCI-native metadata inside the adapter.

## Design

Any OCI-specific conditional limitation must fail closed and remain explicit rather than inventing
false cross-provider parity.

## Implementation Notes

_Pending._

## Review Log

_Pending._
