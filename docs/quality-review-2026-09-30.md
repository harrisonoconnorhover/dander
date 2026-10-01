# Code quality review — September 30, 2026

Reviewed from `425045ff2bbd5ba8b6d6d8eec3c28fb0f59bf525`, with changes tracked in
[DANDER-285](../tickets/DANDER-285-quality-review-command-boundaries.md).

## Assessment and scope

The codebase has a sound core: typed domain contracts, explicit provider boundaries, conditional
storage updates, and substantial tests. Its main maintenance pressure is concentrated in large
command, orchestration, and provider modules. A wholesale rewrite is not justified by this review.

The inventory covered 1,409 tracked files. Python structural analysis covered the tracked Python
sources and tests; the runtime inventory contained 270 modules and approximately 80,000 lines,
excluding scaffold templates. Searches examined duplicate function bodies, large modules/functions,
broad exception handlers, unfinished markers, typing suppressions, and subprocess boundaries.

Manual review followed selected paths through CLI dispatch, plugin loading, Control HTTP and run
storage, graph mutation, pipeline execution, ingestion, warehouse staging, assertions, BigQuery
telemetry/fencing, and CI scope selection. This combines a whole-repository scan with targeted
reading; it is not a line-by-line review of every file or new live-cloud qualification.

## Changes

| Finding | Change | Evidence |
| --- | --- | --- |
| `plugins search` loaded 13 bootstrap, 3 deployment, and 55 provider modules via the root CLI. Offline discovery depended on unrelated command code. | Move install/scaffold/search into `cli/plugins_command.py`; dispatch the plugin group directly and load project configuration only for installation. | New subprocess tests failed before the change and pass afterward. Help/search import none of those module families. Existing CLI tests retain exact pins, version-2 manifests, scaffold behavior, and catalog filtering. |
| S3 and PostgreSQL stores duplicated the same 14-field durable-identity comparison. A future change could update only one implementation. | Put `same_run_identity` beside the `RunStore` contract and remove the duplicated implementations and redundant run-ID checks. | Both backend tests reject a changed plan revision, retain the original stored snapshot, and still accept lifecycle transitions with conditional revisions. |

No dependency, schema, provider payload, or connector package pin changed.

## Existing protections confirmed

- Object-store graph mutation rules and generic assertion planning already have shared owners.
- BigQuery ingestion and graph work already use the shared job-measurement collector.
- The inspected Control mutation routes offload synchronous storage work from the async handler.
- PostgreSQL recovery has a pending-run query; it does not rely on scanning all completed history.
- CI scope detection includes deleted files and both sides of renames.
- Reviewed broad exception handlers at plugin and file boundaries convert external failures or
  clean temporary files; they do not justify a blanket exception-handling rewrite.

## Remaining maintenance priorities

1. **CLI and bootstrap composition:** `cli/main.py` still owns several command groups and wide
   bootstrap signatures. When those commands next change, extract one cohesive group or pass the
   existing typed options object through the relevant boundary. Preserve flags and defaults.
2. **Large provider implementations:** warehouse writers and deployment renderers remain large.
   Extract demonstrated shared semantics; keep SQL dialects, transactions, and provider-specific
   recovery explicit. Similar-looking SQL is not enough reason to add a generic writer framework.
3. **Support evidence:** passing tests do not close the separately tracked curated-package
   compatibility release or prove new cloud configurations. Use [support status](support-status.md)
   and [execution history](control-execution-history.md) for those boundaries.

## Verification

- Baseline: 2,416 tests passed with a disposable local PostgreSQL 17 database; Ruff lint/format,
  canonical strict typing, and generated Control contract drift checks passed.
- Changed-path validation: 75 plugin CLI, console dispatch, S3/PostgreSQL storage, and lifecycle
  tests passed. The new import tests demonstrated the pre-change coupling before passing.
- Final full-suite and protected CI results are recorded in the handoff and pull request.
- The baseline and focused runs emitted one existing Starlette/httpx test-client deprecation
  warning. No live provider workload was submitted by this review.
