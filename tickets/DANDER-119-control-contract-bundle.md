---
id: DANDER-119
title: Publish the Dander Control contract bundle
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-118]
created: 2026-08-13
---

## Context

Druff's manually mirrored Zod schemas have already drifted. Dander must publish the exact API
transport boundary before hosted behavior expands.

## Acceptance Criteria

- [ ] Publish deterministic `io.dander.control.contracts/v1` schemas and canonical fixtures for
      graphs, errors, catalogs, preview, runs, bounded logs, mutations, and compatibility.
- [ ] Encode strict graph boundaries, typed `type`/`config` branches, extensible fallbacks, aliases,
      omission rules, provider extensions, and writer transports in explicit transport DTOs.
- [ ] Record the canonical bundle SHA-256 in a reviewed Dander release artifact.
- [ ] Domain-to-transport and round-trip fixtures pass; drift fails CI.
- [ ] No semantic rule or provider implementation moves into the transport layer.

## Design

Use current Pydantic/domain types as source inputs, but do not claim today's incomplete
`PipelineGraph.model_json_schema()` is the transport contract. Keep semantic validators server-side.

## Implementation Notes

_Pending._

## Review Log

_Pending._
