# Morning Handoff

## Finished

- Reviewed all 16 retained GCP executions from August 20–23 and recorded sanitized evidence in issue #26.
- Paused only the unavailable ServiceNow schedule through a reviewed one-change Terraform plan.
- Preserved the ServiceNow job, alerts, secrets, data, and historical executions.
- Verified the retained platform has no Terraform drift after the scope change.

## Try It

Review issue #26 and confirm the three available schedules remain enabled in `us-central1`.

## Checks

- Greenhouse, HubSpot, and Salesforce passed all 12 scheduled executions reviewed.
- ServiceNow's four failures retained the existing external OAuth-path diagnosis with zero data movement.
- Row/key counts match, all current leases are released, no temporary staging remains, and Terraform reports no changes.

## Decisions

- Continue the original August 2–September 1 soak without the unavailable ServiceNow PDI.
- Evaluate the final seven clean days against Greenhouse, HubSpot, and Salesforce; do not restart the window.
- Treat ServiceNow availability as an external limitation, not a Dander product defect.

## Remaining

- Perform the next weekly retained-run review.
- Close the soak only after the September 1 gate and final seven clean days.
- Reconcile delayed provider costs without rerunning accepted workloads.
- Continue eligible Phase 8 matrix and audit work.

## Review First

- `tickets/DANDER-207-phase8-soak-release.md`
- `docs/operator-soak.md`
- Issue #26 review dated 2026-08-23
