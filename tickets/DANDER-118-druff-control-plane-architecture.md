---
id: DANDER-118
title: Define the bounded Druff control-plane architecture
status: done
component: docs
epic: druff-control-plane
depends_on: [DANDER-117]
created: 2026-08-13
---

## Context

Phase D0 must establish the cross-repository boundary after Phase 7 without consuming or changing
Phase 8. Dander remains the semantic, provider, execution, state, and deployment authority.

## Acceptance Criteria

- [x] Exact Dander and Druff remotes, commits, CI, protection, and Phase 7 cleanup are recorded.
- [x] Existing APIs, domain operations, filesystem coupling, schema drift, static hosting, OIDC,
      provider reuse, service deployment, and Phase 8 freeze boundaries are assessed.
- [x] Contract, storage, compatibility, PR, live-proof, and non-goal strategies are explicit.
- [x] An independent adversarial architecture review is incorporated.
- [x] No application code, provider resource, release, plan, or state is changed.

## Design

See `docs/druff-control-plane-roadmap.md` and the paired Druff Phase D0 roadmap.

## Implementation Notes

The checkpoint retained static Druff plus an authenticated Dander Control API. Review corrections
require an explicit transport DTO bundle, one typed source for public/server trust configuration,
the actual logical-project/platform versions, and GraphStore-first multi-graph sequencing.

## Review Log

### 2026-08-13 — PASS

The corrected roadmap preserves every mandated product boundary and introduces no application or
cloud mutation.
