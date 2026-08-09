# DANDER-102 — PostgreSQL matrix and local benchmark evidence

## Scope

- Publish the complete current BigQuery/PostgreSQL state/warehouse matrix from the installed CLI.
- Fail before provider construction for absent or unsupported pairs.
- Prove BigQuery-issued lease authority can fence PostgreSQL publication and reject a stale token.
- Add a reproducible local bounded-batch/concurrency benchmark without claiming live scale.

## Acceptance

- All four current backend pairs have one explicit status and reason.
- BigQuery-state/PostgreSQL-warehouse publishes through the PostgreSQL target fence.
- PostgreSQL-state/BigQuery-warehouse remains fail-closed.
- The benchmark reports rows, logical bytes, duration, throughput, peak RSS, concurrency, stale
  rejection, staging residue, qualification status, and cost status without DSNs or row values.
- Local smoke results stay `not_evaluated` unless an enforced memory limit is supplied and both SLO
  thresholds pass.

## Exclusions

No cloud deployment, support promotion, paid scale run, adaptive batching, new warehouse mode, or
PostgreSQL-state/BigQuery enablement belongs in this ticket.
