# Post-release operator soak

The 30-day soak began after `0.1.0` became public and continues on the newest supported release. It
gathers operating evidence; it is not a retroactive release gate. Authenticated HubSpot is the
primary pipeline, public Greenhouse is the daily control, and Salesforce and ServiceNow exercise
additional authenticated enterprise paths.

## Operating cadence

- Keep all four retained schedules enabled and review Dander run history once each week.
- Investigate every alert. Wait for any active execution to finish, follow the published upgrade
  and rerun guide, and perform at most one documented rerun for the incident.
- During the soak, perform one planned manual rerun of each enabled pipeline.
- Never edit leases, watermarks, run-scoped staging tables, or Terraform state by hand.
- Record sanitized dates, package versions, run IDs, outcomes, alerts, diagnoses, reruns, row-count
  checks, cursor checks, cleanup checks, and Terraform-plan results in one operator-trial issue.
  Do not include source rows, secret values, state, personal email addresses, or recovery codes.

## Success criteria

Close the trial only when all of the following are true:

- No failed execution was silent, and every failure was diagnosable from the published run ledger,
  Cloud Run logs, and public documentation.
- HubSpot and Salesforce remained duplicate-free, watermarks were monotonic where supported, and
  no lease or staging residue required manual repair.
- Every enabled pipeline was manually rerun once, scheduler state matches the tracked manifest,
  and the final Terraform plan reports no changes.
- The workflow was operated entirely from public packages and documentation.
- The newest supported patch has run cleanly for at least seven consecutive days.

A runtime or Terraform fix becomes a normal patch on the current supported minor and does not
restart the entire 30 days. Documentation-only corrections do not affect the soak. A patch resets
only the final seven-day clean window.
