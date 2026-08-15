---
id: DANDER-207
title: Soak and release the qualified support matrix
status: open
component: release
epic: cloud-portability-phase-8
depends_on: [DANDER-201, DANDER-204, DANDER-205, DANDER-206]
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

## Implementation Notes

- Exact private RC22 is installed on all five retained GCP jobs. One authenticated Salesforce
  manual/replay pair and one Scheduler-created Greenhouse execution passed with equal replay
  counts, clean leases/staging, and a final 113-resource no-drift plan.
- The four daily schedules remain enabled and the graph schedule remains intentionally paused.
  This begins exact-candidate observation; it does not satisfy the 30-day or final seven-clean-day
  criteria, and provider-measured cost is still pending.
