---
id: DANDER-123
title: Add S3 GraphStore
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Add S3 storage behind the accepted GraphStore semantics.

## Acceptance Criteria

- [ ] Use provider-native conditional/version controls and bounded list pagination.
- [ ] Pass shared mock conformance and a separately approved live restart/conflict/cleanup proof.
- [ ] Keep credentials, rows, plans, state, and provider-native revisions out of committed evidence.

## Design

Resolve any narrow S3 conditional-write quirk inside this adapter without weakening GraphStore.

## Implementation Notes

_Pending._

## Review Log

_Pending._
