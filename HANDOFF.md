# Morning Handoff

## Finished

- Published public RC15 runtime/controller artifacts and deployed their exact immutable digests.
- Passed local runtime conformance and the live OCI/PostgreSQL profile with 17 rows and 3 assertions.
- Passed replay, maximum-parallelism-one overlap fencing, active cancellation, and disposable cleanup.
- Reproduced the scheduled start failure as an Oracle work-request `404` before any container existed.
- Live retry proof exposed and PR #248 fixed the launcher-attempt/run-ledger integration boundary.

## Try It

Run `uv run python scripts/check_release_metadata.py` and `uv run pytest -q tests/test_release_metadata.py`.

## Checks

- `uv run pytest tests/bootstrap/test_oci_terraform.py -q` — 12 passed.
- `uv run ruff check tests/bootstrap/test_oci_terraform.py` — passed.
- `terraform fmt -check -recursive infra/oci` — passed.
- RC17 release metadata, wheel, and source distribution validation — passed.
- Both deployed OCI Terraform roots verified no drift before the live proofs.

## Decisions

- Grant the single-schedule dynamic group Oracle's required Function-family verb only in the runtime compartment.
- Keep the schedule inactive until the protected correction is released and its scheduled proof passes.

## Remaining

- Publish RC17, promote its exact artifacts, then re-run the bounded retry proof.
- Complete OCI retry, secret rotation, rollback, alert, cleanup, and final no-drift proofs.
- Complete Phase 7 evidence and binary exit-gate recommendation.
- Complete Phase 8 qualification within approved ceilings.

## Review First

- `CHANGELOG.md`
- `pyproject.toml`
- `tests/test_release_metadata.py`
