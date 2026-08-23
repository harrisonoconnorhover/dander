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

The GCP operator trial remains open through 2026-09-01. Its ServiceNow PDI became unavailable, and
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
- Greenhouse, HubSpot, and Salesforce remain enabled daily; ServiceNow and the graph schedule are
  paused. The 2026-08-23 review covered every Aug. 20–23 execution, retained all four ServiceNow
  failures and their existing external OAuth-path diagnosis, and found no new Dander defect.
- The ServiceNow schedule was paused through an exact one-change retained-manifest Terraform plan.
  Its job, alerts, secrets, data, and historical evidence remain, and the post-apply plan reported
  no changes. The original 2026-08-02 through 2026-09-01 window is unchanged; the final seven clean
  days evaluate the three remaining enabled schedules. See the sanitized review in issue #26.
