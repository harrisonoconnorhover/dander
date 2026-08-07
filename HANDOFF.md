# Morning Handoff

## Finished

- Added OCI source, documentation, license, version, revision, and creation annotations.
- Packaged and validated `io.dander.runtime.capabilities/v1` through `runtime inspect`.
- Made image publication attach SBOM/provenance and record index plus runnable-platform digests.
- Made artifact-record writes atomic and exposed their path through `dander image-publish`.
- Added CI execution of runtime conformance as UID 65532 on a read-only root filesystem.

## Try It

Build the root Dockerfile with `DANDER_BUILD_REVISION` and `DANDER_BUILD_CREATED`, then run
`docker run --rm --read-only --tmpfs /tmp IMAGE runtime conformance`. A real
`dander image-publish` writes `.dander/runtime-artifact.json` after registry verification.

## Checks

- Ruff format/lint and strict mypy passed.
- All 804 tests passed.
- Root and stage-zero Terraform formatting, backend-disabled initialization, and validation passed.
- Wheel/sdist inspection confirmed the packaged capability manifest.
- A local OCI image passed inspection and conformance as non-root with a read-only root filesystem.

## Decisions

- The source-free build-context digest is the artifact revision; the registry digest remains the
  immutable deployment identity.
- Attestation descriptors use `unknown/unknown` and are excluded from runnable-platform records.
- Publication fails closed after push if the registry manifest or local artifact record is invalid.

## Remaining

- Merge this focused ticket through protected main.
- Add the cloud-neutral execution projection.
- Make Cloud Run consume the projection and prove GCP parity.
- Add multi-architecture build/copy support in its separately planned phase.

## Review First

- `src/dander/bootstrap/project.py`
- `src/dander/runtime_inspection.py`
- `.github/workflows/ci.yml`
