---
id: DANDER-80
title: Add canonical relation and schema contracts
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-79]
created: 2026-08-07
---

## Context

BigQuery project/dataset/table coordinates and type/mode strings cannot remain shared warehouse
boundaries. The portable contract must retain exact semantics before provider-specific rendering.

## Acceptance Criteria

- [x] `RelationRef` keeps catalog, namespace, and name unrendered.
- [x] `RelationCodec` makes provider rendering an explicit adapter responsibility.
- [x] Canonical types cover required scalar, decimal, timestamp, array, and record semantics.
- [x] Relation and nested-record fields reject duplicate names.
- [x] Provider extensions are validated, ordered, and duplicate-free.
- [x] Existing BigQuery raw and writer fields expose a one-way canonical view.
- [x] Unsupported BigQuery types fail without an explicit fallback.
- [x] Existing authored schemas and BigQuery execution remain unchanged.
- [x] Full local validation passes.
- [ ] Protected CI passes.

## Design

Use frozen Pydantic models so canonical contracts serialize deterministically but still parse normal
YAML/JSON scalar forms. Keep BigQuery compatibility in a one-way mapper accepting the structural
shape shared by `RawField` and `WriteField`.

## Implementation Notes

This PR does not add a provider codec implementation, rewrite schema YAML, or route a runtime
through canonical writers. Those moves remain separately characterized adapter work.

## Review Log

Pending protected review.
