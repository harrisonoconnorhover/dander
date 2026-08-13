# Morning Handoff

## Finished

- Published public RC15 runtime/controller artifacts and deployed their exact immutable digests.
- Passed local runtime conformance and the live OCI/PostgreSQL profile with 17 rows and 3 assertions.
- Passed replay, maximum-parallelism-one overlap fencing, active cancellation, and disposable cleanup.
- Reproduced the scheduled start failure as an Oracle work-request `404` before any container existed.
- Corrected the scheduler dynamic-group permission to Oracle's required compartment-scoped `manage functions-family`.

## Try It

Run `uv run pytest tests/bootstrap/test_oci_terraform.py -q`.

## Checks

- `uv run pytest tests/bootstrap/test_oci_terraform.py -q` — 12 passed.
- `uv run ruff check tests/bootstrap/test_oci_terraform.py` — passed.
- `terraform fmt -check -recursive infra/oci` — passed.
- Both deployed OCI Terraform roots verified no drift before the live proofs.

## Decisions

- Grant the single-schedule dynamic group Oracle's required Function-family verb only in the runtime compartment.
- Keep the schedule inactive until the protected correction is released and its scheduled proof passes.

## Remaining

- Merge and release the scheduler permission correction, then re-run the scheduled proof.
- Complete OCI retry, secret rotation, rollback, alert, cleanup, and final no-drift proofs.
- Complete Phase 7 evidence and binary exit-gate recommendation.
- Complete Phase 8 qualification within approved ceilings.

## Review First

- `infra/oci/main.tf`
- `tests/bootstrap/test_oci_terraform.py`
- `docs/decisions.md`
