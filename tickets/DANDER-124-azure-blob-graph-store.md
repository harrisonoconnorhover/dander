---
id: DANDER-124
title: Add Azure Blob GraphStore
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Add Azure Blob Storage behind the accepted GraphStore semantics.

## Acceptance Criteria

- [ ] Use Blob ETag conditional controls and bounded list pagination.
- [ ] Pass shared mock conformance and a separately approved live restart/conflict/cleanup proof.
- [ ] Keep managed identity and Blob-native metadata inside Dander/provider boundaries.

## Design

The API exposes opaque revisions and canonical hashes, never Azure request payloads.

## Implementation Notes

_Pending._

## Review Log

_Pending._
