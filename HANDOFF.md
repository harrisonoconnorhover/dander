# Morning Handoff

## Finished

- Retired expired DANDER-237/238 staging through its existing Terraform state: two ECS services,
  the load balancer/listener/rule, and public CloudFront routing are gone.
- Verified zero Control tasks/services/network interfaces; preserved the versioned graph store,
  all six durable run records, accepted images, and historical evidence.
- Merged BigQuery graph and SCD1 job measurements through
  [PR #534](https://github.com/harrisonoconnorhover/dander/pull/534), using one shared normalizer.
- Updated the main checkout and preserved the original HDFS branch.

## Try It

Run `uv sync --frozen --extra dev --extra postgres`, then follow
[Control profiles](docs/control-profiles.md). New current-source workers emit job measurements;
previously deployed images and historical results retain their original behavior.

## Checks

- 110 focused tests passed; Ruff lint/format and strict typing across 494 files passed.
- All six protected PR checks and exact-main CI checks at `1aa1f60` passed:
  [run](https://github.com/harrisonoconnorhover/dander/actions/runs/34164220943).
- Readback preserved actual counters from the three completed graph jobs and the output-comparison
  job. No new query or worker execution was submitted.
- Reviewed six-resource Terraform retirement and direct AWS absence checks passed. Six run records
  matched their preserved copies byte for byte after retirement.
- Focused document links, handoff structure, and diff checks passed.

## Decisions

- Count completed parent jobs once and drain ingestion statistics once per batch. The collector
  covers graph and SCD1 paths; operation measurements are not a complete provider invoice.
- Preserve storage, logs, IAM, empty clusters, and launcher foundations. Historical Terraform
  inputs would recreate retired staging on a full apply; the operator folder records retirement.

## Remaining

- Reconcile retained storage, keys, and registry costs before new paid qualification. The retired
  service categories showed about USD 2.80/day; savings are estimated and billing remains delayed.
- Hadoop qualification needs an existing secure cluster and access details.
- DANDER-207 scale/cost, pairwise, other-profile soak, audit, and release gates remain open.

## Review First

- [Measurement coverage](tickets/DANDER-284-bigquery-graph-job-telemetry.md)
- [Current support and next steps](docs/support-status.md)
- Private retirement results: `/Users/harrison/.codex/operator/dander-cost-cleanup-20260907`
