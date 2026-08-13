---
id: DANDER-127
title: Add the Dander control-service projection
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-121, DANDER-126]
created: 2026-08-13
---

## Context

Long-running Dander Control API services have current deployment needs distinct from job launchers
and from Druff's static artifact.

## Acceptance Criteria

- [ ] Add immutable resolved request/template contracts for image/command/port/probes/resources/
      scaling/shutdown, environment, secret refs, identity, ingress/origins, GraphStore,
      observability, and rollback digest.
- [ ] Add an internal service provider kind/factory with lazy selected-provider loading.
- [ ] Keep job launcher contracts and projections byte/semantically unchanged.
- [ ] Keep Druff `StaticAssetBundle` as a separate deployment input with digest, entrypoint,
      bootstrap digest, and security headers.
- [ ] Deterministic projection and fail-before-provider-access tests pass.

## Design

Provider TLS, networking, IAM, load balancers, and native resource IDs remain in provider modules;
this is not a universal service framework.

## Implementation Notes

_Pending._

## Review Log

_Pending._
