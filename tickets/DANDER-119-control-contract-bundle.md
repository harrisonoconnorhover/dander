---
id: DANDER-119
title: Publish the Dander Control contract bundle
status: complete
component: python
epic: druff-control-plane
depends_on: [DANDER-118]
created: 2026-08-13
---

## Context

Druff's manually mirrored Zod schemas have already drifted. Dander must publish the exact API
transport boundary before hosted behavior expands.

## Acceptance Criteria

- [x] Publish deterministic `io.dander.control.contracts/v1` schemas and canonical fixtures for
      graphs, errors, catalogs, preview, runs, bounded logs, mutations, and compatibility.
- [x] Encode strict graph boundaries, typed `type`/`config` branches, extensible fallbacks, aliases,
      omission rules, provider extensions, and writer transports in explicit transport DTOs.
- [x] Record the canonical bundle SHA-256 in a reviewed Dander release artifact.
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
- Wheel/sdist checks require the manifest and representative schema. The separately approved
  protected `0.9.0rc18` release contains this exact digest at immutable tag `v0.9.0rc18` and
  protected-main commit `ae2f8f6bfda5fe54309c54eee623b83d0b2bd2a3`. Trusted-publishing run
  `31719571923` uploaded wheel
  `sha256:4500b32451c02b6331a337b6d38eb96cc49a29838b6e3ea5a2b87b9daf85406c` and source
  distribution `sha256:bf5ead721ab2b61eff4b50be5c3ab9cb03edb59257c0b2a3f1c0019c7045c3ae`.
- A fresh PyPI-only install outside a checkout verified the CLI, starter project, Terraform
  configuration, manifest digest, and all 25 installed bundle-file hashes.

## Review Log

- 2026-08-13: pre-implementation adversarial review required independent JSON Schema validation,
  stable schema identity, negative artifact cases, and an honest unpublished-release boundary;
  all four corrections were incorporated.
- 2026-08-13: completion review found that writer extras could be discarded by the domain model;
  the writer DTO was made strict and DTO/schema rejection tests were added.
- 2026-08-13: protected promotion PR #254 and its post-merge CI passed; explicit approval then
  produced the immutable tag, trusted PyPI publication, matching GitHub prerelease, and successful
  outside-checkout verification.
