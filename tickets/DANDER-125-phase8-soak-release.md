---
id: DANDER-125
title: Soak and release the qualified support matrix
status: open
component: release
epic: cloud-portability-phase-8
depends_on: [DANDER-119, DANDER-122, DANDER-123, DANDER-124]
created: 2026-08-13
---

## Context

The GCP operator trial remains open through 2026-09-01, two failures are not yet diagnosable, and
other canonical profiles have not completed an exact-candidate scheduled soak.

## Acceptance Criteria

- [ ] Every approved profile completes its reviewed scheduled candidate soak with visible failures,
  cleanup, and no drift.
- [ ] The retained GCP trial meets its 30-day and final seven-clean-day criteria after diagnostics.
- [ ] Current scale/cost, pairwise, lifecycle, and audit evidence is attached.
- [ ] Compatibility and known limitations freeze to the tested support statuses.
- [ ] No material independent-review blocker remains before public support release.
