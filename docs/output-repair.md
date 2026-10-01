# Repair selected output dates

Current source implements date-scoped output repair for direct Cloud Run execution with a
BigQuery warehouse. It rebuilds selected dates from the raw data **already stored** in the
warehouse. It does not fetch source records, advance ingestion progress, or reconstruct a past
source snapshot. The published RC19/RC20 client/runtime pair does not include this operation.

## Preview, then start

1. Read the saved graph and retain its `ETag`.
2. POST the date window to `/v1/projects/{project}/graphs/{graph}/repair-preview` with that
   `If-Match` value. Review the outputs, UTC date boundary, available execution environments,
   and limitations. This preview does not read warehouse rows or start a workload.
3. POST the same window to `/v1/projects/{project}/graphs/{graph}/repairs` with `If-Match` and
   a new `Idempotency-Key`. When the preview offers more than one environment, select one through
   `?environment=...`. Reuse the same key and body if a response is lost.
4. Poll the returned run through `/v1/runs/{run_id}` and `/v1/runs/{run_id}/explanation`.
   The status retains `repair_window`. Existing cancellation, history, and replay controls apply;
   replay retains the window but uses the raw data available when it runs.

For September 1 and 2, send:

```json
{"start_date": "2026-09-01", "end_date": "2026-09-03"}
```

The start date is included and the end date is excluded. For `TIMESTAMP` fields, dates are
interpreted in UTC. An editor can preview; starting a repair requires the operator role.

## Eligible outputs and write behavior

Every configured output must declare a `DATE` or `TIMESTAMP` partition field and a scalar business
key. Output tables must already exist. The first slice retains the graph's existing replace
configuration, but repair publication replaces only the selected dates. It requires the normal
BigQuery lease and fencing; sandbox execution and other warehouse/execution backends reject repair.
Outputs may not overlap each other or their raw input tables.

The runtime stages the current graph result before publishing. It checks selected keys and rejects
a key that moves across the date boundary in either direction, including an undated counterpart.
That failure leaves the affected output unchanged; choose a wider range or do a full rebuild.
Rows outside the interval remain untouched. Empty selected results remove only selected dates.

Each output publishes in one fenced transaction. If a later output fails, earlier successful outputs
remain; retrying converges on the current raw data. Publication counts appear in run telemetry and
the explanation. The existing ingestion counters stay zero because extraction was skipped.
Computing the result can scan more data than the dates being replaced; the preview cannot quote
row counts or cost without warehouse work.

## Runtime compatibility and verification

Control binds the exact dates and graph hash to the deterministic Cloud Run execution. The fixed
repair contract argument makes an old runtime reject the request before pipeline work, rather
than silently executing a full replacement. Adoption checks the exact execution payload. A
different ordinary execution ignores a well-formed inherited selection; Control also restores
the base Job settings after terminal repair while they still belong to that repair.

Local checks cover date boundaries, unchanged surrounding rows, collision rejection, transaction
rollback, lease fencing, no source/watermark access, replay identity, and Cloud Run adoption and
cleanup. The row checks execute translated predicates against SQLite; they do not establish live
BigQuery or Cloud Run qualification. A bounded synthetic live check and the paired Druff interface
remain required before claiming the complete guided workflow is delivered.
