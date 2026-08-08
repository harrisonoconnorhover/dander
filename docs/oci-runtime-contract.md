# OCI Runtime Contract v1

`io.dander.runtime/v1` is the stable boundary between a launcher and one Dander pipeline process.
It does not provision infrastructure and it does not call launcher APIs.

## Invocation

```text
dander runtime execute \
  --contract io.dander.runtime/v1 \
  --pipeline PIPELINE_ID \
  --platform gcp \
  --config /app/dander.yaml
```

Phase 1 accepts only the existing `gcp` compatibility profile. Later profiles must enter through
the same versioned command after their platform configuration and adapters are implemented.

Launchers may set these non-secret correlation values:

| Variable | Default | Validation |
|---|---|---|
| `DANDER_RUN_ID` | launcher execution ID or a generated UUID | 1–128 identifier characters |
| `DANDER_LAUNCHER` | `cloud_run` when Cloud Run context exists, otherwise `local` | 1–64 identifier characters |
| `DANDER_LAUNCHER_EXECUTION_ID` | Cloud Run execution ID when present | 1–256 characters, no control characters |
| `DANDER_ATTEMPT` | one-based Cloud Run attempt or `1` | integer 1–1000 |
| `DANDER_SHARD_INDEX` | Cloud Run task index or `0` | integer 0–9999 and less than shard count |
| `DANDER_SHARD_COUNT` | Cloud Run task count or `1` | integer 1–10000 |
| `DANDER_DEADLINE_AT` | absent | timezone-aware ISO-8601 timestamp |
| `DANDER_PRINCIPAL` | absent | 1–256 characters, no control characters |

These values are identifiers and context, never secret material. A launcher must not place tokens,
credentials, source rows, SQL, or request bodies in them.

## Events

Standard output contains JSON Lines. A validated invocation emits `runtime.started`, followed by
exactly one `runtime.completed` when the process can terminate normally. The terminal status is
`succeeded`, `skipped`, or `failed` and includes only aggregate counts, stable failure codes, and a
retryability decision. Cursor values, record contents, credential material, and unrestricted
exception text are excluded.

Every terminal event includes `outputs.telemetry`. The provider-neutral shape records whole-run
`duration_ms` and ordered operation statistics for retries, rows, bytes, provider query/job IDs,
and cost attribution. Fields not reported by the selected adapter remain zero or absent; Dander
does not infer provider billing. Cost values are decimal strings with an explicit currency and
`estimated` marker. Query and job IDs are correlation identifiers only—adapters must never put
SQL, request bodies, record contents, URLs, or credentials in telemetry.

Successful ingestion row counts remain in `outputs.metrics` and `outputs.endpoints` for runtime-v1
compatibility. Detailed operation telemetry is additive and does not change the stable process
exit or retry contract.

Invalid contract or identifier input fails before `runtime.started`. `SIGTERM` and `SIGINT` request
bounded normal cleanup and produce `interrupted_run` when time remains. `SIGKILL` cannot produce a
terminal event; lease expiry and the next owner reconcile that run.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | succeeded or intentionally skipped |
| 1 | permanent runtime failure |
| 2 | invalid invocation or configuration |
| 75 | retryable runtime failure |
| 130 | graceful cancellation requested |

Launchers own their finite whole-process retry budget. An exit code never permits an unbounded
retry loop.

## Inspection and local conformance

`dander runtime inspect --config dander.yaml` reports the installed Dander version, active
compatibility adapters, ingestion engines, and explicitly pinned connector plugins. It validates
package entry points but never constructs a source, resolves a secret, or contacts a provider.

`dander runtime conformance` runs one deterministic executor lifecycle with local SQLite state,
parses the versioned start/completion events, and verifies graceful `SIGTERM` translation. With an
explicit empty `--work-dir`, its only filesystem write is `state.db`; without one it uses and
removes a temporary directory. The probe uses no credentials, network, or cloud resources.
