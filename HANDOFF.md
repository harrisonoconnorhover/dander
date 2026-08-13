# Morning Handoff

## Finished

- Prepared public `dander-platform==0.9.0rc12` from protected main.
- Published and deployed exact RC11 runtime/controller artifacts from protected main.
- Created the free PostgreSQL qualification project and versionless OCI Vault secret.
- Verified stage-zero and launcher Terraform at no drift with the schedule inactive.
- Ran the first live invocation; OCI rejected E4 creation before billing because account quota is zero.

## Try It

Run `terraform test -test-directory=tests` from `infra/oci`, then `uv run pytest -q tests/providers/test_oci_function_handler.py`.

## Checks

- RC11 protected-main CI, PyPI publication, OCIR promotion, and exact launcher verification passed.
- Focused controller tests passed (22); OCI Terraform tests passed (2).
- The rejected E4 attempt created no Container Instance and retained a sanitized terminal record.

## Decisions

- Use the supported A1 profile because this account has A1 quota and zero E4 quota.
- Preserve the schedule as inactive until the controlled scheduled-run proof.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge and publish `v0.9.0rc12` through protected CI, then promote its exact artifacts.
- Apply the reviewed A1 projection and controller/policy correction.
- Complete success, rotation, overlap, retry, replay, cancel, schedule, and rollback proofs.
- Complete the bounded live lifecycle acceptance matrix.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/providers/oci_container_instances/function_handler.py`
- `infra/oci/main.tf`
- `HANDOFF.md`
