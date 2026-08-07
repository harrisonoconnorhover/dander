---
id: DANDER-78
title: Separate logical projects from named platform deployments
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-77]
created: 2026-08-07
---

## Context

Phase 2 begins by separating portable pipeline intent from environment and launcher projection
without changing the proven version 1 GCP deployment or claiming another provider is supported.

## Acceptance Criteria

- [x] Version 2 `dander.yaml` contains only plugins and logical pipeline intent.
- [x] Version 1 `dander.platforms.yaml` validates named platform profiles and deployments.
- [x] Multiple deployments require explicit selection; one deployment resolves deterministically.
- [x] Version 1 combined manifests remain compatible.
- [x] `dander config migrate --check` proves equivalent GCP behavior without writing.
- [x] Migration writes both files deterministically and refuses an existing platform file.
- [x] Newly generated source-free projects include and copy both files.
- [x] Full local tests, lint, typing, packaging, and Terraform validation pass.
- [x] Existing isolated GCP configuration produces a no-change Terraform plan before and after migration.
- [ ] Protected CI passes.

## Design

Keep the existing `DanderProject` as a resolved compatibility view consumed by current execution
and Terraform code. Parse v2 logical and platform files through closed Pydantic models, select one
deployment, and flatten only that deployment into the compatibility view. Add provider factories
and broader provider schemas in later reviewable tickets rather than implying support here.

## Implementation Notes

The migration materializes existing defaults, maps `publish_dataplex` to portable
`publish_catalog`, and verifies platform, plugin, pipeline, stable-name, and Terraform-projection
equality. The standard scaffold now authors v2; the repository's retained v1 manifest is unchanged.

## Review Log

Pending protected review.
