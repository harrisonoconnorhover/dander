---
id: DANDER-119
title: Publish the Dander Control contract bundle
status: in_progress
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
- [x] Encode strict graph boundaries, typed `type`/`config` branches, extensible fallbacks, aliases,
      omission rules, provider extensions, and writer transports in explicit transport DTOs.
- [ ] Record the canonical bundle SHA-256 in a reviewed Dander release artifact.
- [x] Domain-to-transport and round-trip fixtures pass; drift fails CI.
- [x] No semantic rule or provider implementation moves into the transport layer.

## Design

Use current Pydantic/domain types as source inputs, but do not claim today's incomplete
`PipelineGraph.model_json_schema()` is the transport contract. Keep semantic validators server-side.

## Implementation Notes

- Explicit immutable DTOs live in `src/dander/control/models.py`; graph construction revalidates
  through the canonical domain model and wiring checks.
- The deterministic builder emits Draft 2020-12 schemas with stable URN IDs, internal references,
  canonical fixtures, per-file hashes, and bundle digest
  `344ef5ff2d685d5bedf7a1ddb119a42a6de08d90f285dc0a981e79c55452c1ed`.
- Artifact tests independently validate emitted schemas, positive extension preservation, and
  negative known-config, operation-parameter, strict-extra, and authored-`direct` cases.
- Wheel/sdist checks require the manifest and representative schema. The bundle is release-ready
  but unpublished; the first and third criteria stay open until a separately approved protected
  Dander release contains this exact digest.

## Review Log

- 2026-08-13: pre-implementation adversarial review required independent JSON Schema validation,
  stable schema identity, negative artifact cases, and an honest unpublished-release boundary;
  all four corrections were incorporated.
- 2026-08-13: completion review found that writer extras could be discarded by the domain model;
  the writer DTO was made strict and DTO/schema rejection tests were added.
