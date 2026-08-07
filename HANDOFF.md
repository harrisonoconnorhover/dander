# Morning Handoff

## Finished

- Added the launcher-neutral `io.dander.runtime/v1` invocation and JSON-Line event contract.
- Added stable success, invalid, permanent, retryable, and cancellation exit codes.
- Preserved launcher run IDs through the existing executor and run ledger.
- Added validated Cloud Run/local context, one-based attempt normalization, and bounded signal cleanup.
- Kept ordinary `dander run` behavior and the GCP compatibility profile unchanged.

## Try It

Run `dander runtime execute --contract io.dander.runtime/v1 --pipeline PIPELINE --platform gcp`
inside a generated project. See `docs/oci-runtime-contract.md` for launcher variables and outputs.

## Checks

- Ruff lint/format and strict mypy passed.
- All 790 tests passed.
- Root and stage-zero Terraform format, backend-disabled initialization, and validation passed.
- Wheel/sdist inspection and source-free wheel installation with `dander runtime --help` passed.

## Decisions

- Runtime events contain identifiers and aggregate counts only; unrestricted exception text,
  cursor values, rows, credentials, and query bodies remain outside the contract.
- Cloud Run's zero-based task attempt is normalized to Dander's one-based launcher attempt.
- Phase 1 accepts only the existing `gcp` profile until later profiles pass their own gates.

## Remaining

- Merge this focused runtime-contract ticket through protected main.
- Add `dander runtime inspect` and the credential-free local OCI conformance command separately.
- Add artifact metadata/SBOM, execution projection, then Cloud Run projection parity.

## Review First

- `src/dander/runtime_contract.py`
- `src/dander/cli/runtime_command.py`
- `tests/cli/test_runtime_cli.py`
