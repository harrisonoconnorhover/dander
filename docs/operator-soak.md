# Post-release operator soak

The retained 30-day operator trial ran from 2026-08-02 through 2026-09-01. It gathered operating
evidence and was not a retroactive release gate. Authenticated HubSpot was the primary pipeline,
public Greenhouse was the daily control, and available enterprise sandboxes added authenticated
provider paths.

## Closure outcome

The trial concluded on 2026-09-02 after the final audit and planned HubSpot rerun:

- All 21 enabled scheduled executions succeeded during the final seven days, 2026-08-26 through
  2026-09-01.
- On 2026-09-02, after the observation window, Greenhouse and HubSpot completed on schedule.
  Salesforce encountered repeated BigQuery transaction contention, reached its 600-second task
  limit during transform, and exhausted the existing platform retry. One documented manual
  recovery run then succeeded without contention before the schedules were paused.
- The 2026-08-25 Salesforce failure was visible in the durable ledger and diagnosable from
  sanitized logs as an OAuth HTTP 400 path; later scheduled runs succeeded without data repair.
- Raw and staging row/key counts matched, applicable watermarks did not regress, all current leases
  were released, and no run-scoped staging table remained.
- Planned Greenhouse, HubSpot, and Salesforce reruns completed. ServiceNow was not rerun after its
  external PDI became unavailable; its schedule was excluded on 2026-08-23 while its job, alerts,
  secrets, data, and history were preserved.
- A pre-closure Terraform plan reported no changes. All five retained schedules were then paused
  through reviewed Terraform, and the post-apply plan again reported no changes.

This closes the bounded operator observation with the ServiceNow sandbox limitation recorded. It
does not promote a provider, close Phase 8 qualification, or turn the retained private candidate
into a public release.

## Operating cadence

- Keep every available retained schedule enabled and review Dander run history once each week.
- If an external sandbox becomes unavailable, pause only its schedule through reviewed Terraform,
  preserve its job and evidence, and record the reduced live scope without restarting the window.
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
