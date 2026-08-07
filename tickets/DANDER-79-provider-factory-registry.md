---
id: DANDER-79
title: Add lazy provider factory registries
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-78]
created: 2026-08-07
---

## Context

Platform profiles need one explicit construction boundary before concrete warehouse, state,
catalog, secret, and launcher adapters can move independently. Provider configuration validation
must not import every provider SDK.

## Acceptance Criteria

- [x] All five provider categories use the same API-v1 registry contract.
- [x] Provider IDs and configuration models validate before construction.
- [x] Duplicate and unknown providers fail with stable, non-sensitive errors.
- [x] Provider implementations load only after explicit selection.
- [x] Factories validate category, provider ID, API version, and callable construction.
- [x] Deterministic discovery and lazy-load behavior have focused tests.
- [x] Full local validation passes.
- [ ] Protected CI passes.

## Design

Keep the registry generic over provider products and inject construction dependencies explicitly.
Each concrete adapter owns its typed product and errors. Register lightweight configuration models
now; provider modules and SDK imports stay behind lazy factory loaders.

## Implementation Notes

This PR does not move BigQuery runtime behavior or claim a new provider. Canonical relations and
schemas, adapter bundles, and provider-specific implementations remain separate follow-up changes.

## Review Log

Pending protected review.
