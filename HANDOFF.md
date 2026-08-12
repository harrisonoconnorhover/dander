# Morning Handoff

## Finished

- Prepared public `dander-platform==0.9.0rc11` from protected main.
- Promoted the RC9 source-free runtime into OCIR with an identical OCI index digest.
- Published the exact RC9 wheel-bound lifecycle controller into private OCIR.
- Applied the reviewed additive launcher plan with an inactive schedule.
- Removed the scheduler BODY after live state exposed Oracle's double encoding and drift.

## Try It

Run `terraform test -test-directory=tests` from `infra/oci`, then `pytest -q tests/providers/test_oci_function_handler.py`.

## Checks

- RC9 protected-main CI and trusted PyPI publication passed.
- GAR and OCIR runtime index digests both equal `sha256:5d46e5cf…7a1386`.
- Launcher apply added 11 resources, changed none, and destroyed none; live verification then found
  only the scheduler BODY normalization drift.

## Decisions

- Omit the optional OCI scheduler BODY; the controller's empty request already means `start`.
- Keep manual/event requests explicit and the live schedule inactive until acceptance.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge and publish `v0.9.0rc11` through protected CI.
- Reconcile the one live scheduler resource and prove Terraform no drift.
- Create and rotate only the named free PostgreSQL secret outside Terraform.
- Complete the bounded live lifecycle acceptance matrix.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/providers/oci_container_instances/config.py`
- `src/dander/security/oci_vault.py`
- `HANDOFF.md`
