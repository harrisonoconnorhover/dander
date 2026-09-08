# Morning Handoff

## Finished

- Basic CLI commands and local/PostgreSQL imports keep Google SDKs unloaded; generic OAuth JWT signing uses the existing PyJWT dependency.
- Base and PostgreSQL installations omit Google SDKs. Existing BigQuery/GCP and full-runtime extras retain their provider dependencies; selected BigQuery commands explain missing extras.
- Distribution CI exercises five fresh installation profiles outside the checkout, including actual PostgreSQL adapter imports.
- PostgreSQL recovery queries an index of unfinished runs; conditional writes maintain its eligibility flag atomically and public run history stays complete.
- Schema v4 backfills from validated canonical records without changing their bytes or revisions; migration failures roll back.

## Try It

For development, run `uv sync --frozen --extra dev --extra postgres --extra bigquery --extra gcp`.
For a lean PostgreSQL checkout, use `uv sync --frozen --extra postgres`.
Try `uv run dander control serve --profile examples/control/local.yaml` and stop with Ctrl-C.
See README installation profiles and `docs/control-profiles.md` before upgrading an existing database.

## Checks

- Final integrated suite: 2,416 tests passed with disposable PostgreSQL 15. Focused Control suite also passed 398 tests on PostgreSQL 17. Test containers were removed.
- Canonical strict typing passed for 507 files; Ruff lint/format, Control contract drift, release metadata, and diff checks passed.
- Wheel/source archive validation and all five fresh-install profiles passed after the dependency split. Base and PostgreSQL profiles contained no Google SDKs.
- Migration tests cover backfill/reopen, bytes/revisions, rollback on corruption, conditional-write conflicts, pending-page continuation, complete history, and recovery readiness/fallback.
- Fresh base installation: 97 to 60 distributions; package-file metadata totals 290.1 to 52.3 MiB on this Mac. These are local installation measurements, not deployment memory or cloud cost.

## Decisions

- Keep provider SDK imports and optional-dependency errors at selected provider boundaries; no package versions changed in the lockfile.
- A run needs recovery until execution, outcome, results, and cleanup are resolved; readiness checks recovery, not completed-history integrity.
- Stop the single Control process for the v4 migration. Backfill holds table locks; older binaries reject v4, so mixed-version operation is unsupported.

## Remaining

- No live database upgrade or provider qualification was performed. Existing provider support boundaries and public RC20 remain unchanged.
- A future deployed upgrade needs a maintenance window sized for its saved history and the documented single-process restart.

## Review First

- `src/dander/control/postgresql_control_database.py` and `postgresql_run_store.py`
- `src/dander/control/run_lifecycle.py` and `tests/control/test_postgresql_run_store.py`
- `pyproject.toml`, `scripts/check_provider_installations.py`, and README installation profiles
