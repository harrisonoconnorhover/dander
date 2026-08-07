# GCP Compatibility Baseline

Cloud portability begins from the released `0.6.0` GCP profile. This baseline intentionally makes
no runtime change. It identifies the behavior that each refactor must preserve until a reviewed
profile migration says otherwise.

| Boundary | Compatibility behavior | Direct characterization |
|---|---|---|
| CLI | `dander run PIPELINE` resolves version 1 manifests and executes the named hosted pipeline | `tests/cli/test_run_command.py` |
| BigQuery publication | SCD1 stages with expiration, DML-touches the exact lease token, and merges in the same transaction | `tests/writer/test_bigquery_writer.py` |
| State | run history records one terminal state; leases and watermarks reject stale ownership | `tests/state/test_run_history.py`, `tests/state/test_lease.py`, `tests/state/test_bigquery_watermark.py` |
| Cloud Run | one job and scheduler per pipeline receive the declared resources, retry limit, batch size, and guarded-runtime flag | `tests/infra/test_multi_pipeline_runtime.py` |
| Distribution | `dander-platform` exposes the `dander` CLI and packages every clean Terraform project asset explicitly | `tests/test_distribution_contract.py` |

`tests/portability/test_gcp_compatibility_baseline.py` binds those five boundaries together as the
Phase 0 release invariant. Provider work adds contracts around them; it does not silently alter
their current semantics.
