# Morning Handoff

## Finished

- Prepared public `dander-platform==0.9.0rc9` from protected main.
- Promoted one source-free two-platform runtime into OCIR with an identical OCI index digest.
- Published the exact RC8 wheel-bound lifecycle controller into private OCIR.
- Found the live Vault OCID future-use segment during fail-closed launcher validation.
- Updated Vault configuration and runtime-reference parsing for Oracle's live OCID shape.

## Try It

Run `pytest -q tests/project/test_oci_portable_config.py tests/providers/test_oci_container_instances_runtime.py tests/providers/test_oci_vault_runtime.py`.

## Checks

- RC8 protected-main CI and trusted PyPI publication passed.
- GAR and OCIR runtime index digests both equal `sha256:2ed916d9…50ef3`.
- The launcher plan rejected the previously unmodeled live Vault OCID before any OCI change.

## Decisions

- Allow OCI Vault's observed single future-use OCID segment, plus the prior empty form.
- Keep all other OCI identifier and provider-profile validation unchanged.
- Keep OCI experimental until Phase 7 live acceptance and Phase 8 qualification pass.

## Remaining

- Merge and publish `v0.9.0rc9` through protected CI.
- Review and apply the exact OCI launcher/controller plan.
- Create and rotate only the named free PostgreSQL secret outside Terraform.
- Complete the bounded live lifecycle acceptance matrix.
- Complete cleanup, no-drift evidence, and the binary Phase 7 recommendation.

## Review First

- `src/dander/providers/oci_container_instances/config.py`
- `src/dander/security/oci_vault.py`
- `HANDOFF.md`
