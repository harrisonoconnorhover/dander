# Morning Handoff

## Finished

- Protected the Snowflake identifier correction in PR #360 and preserved the failed RC28 evidence.
- Merged private RC29 preparation in PR #362 as exact main `7a6d138`; all five main jobs passed.
- Published one private source-free amd64/arm64 RC29 GAR index at `sha256:e016419f…aad54`.
- Verified exact wheel, rootless read-only runtime, stable qualification entrypoint, SBOM, and provenance.
- Recorded the sanitized candidate and cost preflight without starting a live provider execution.

## Try It

Inspect `docs/evidence/phase8/2026-08-17/rc29-candidate.json` and compare its digest with GAR.

## Checks

- Protected exact-main CI run `31988620430` passed all five jobs before publication.
- RC29 wheel and source distribution passed `scripts/check_distribution.py`.
- Both image architectures reported RC29 as UID 65532 and passed read-only conformance.
- Both architectures passed the mounted `dander qualification-run` probe.
- Both attestations expose SPDX 2.3 SBOM and SLSA provenance for the exact context revision.

## Decisions

- Preserve RC28 and unaffected evidence; only materially affected Azure correctness reruns now.
- Keep automatic retries disabled and require a protected exact RC29 objective before live execution.
- Hold the USD 2 delayed-billing bound and USD 0.25 publication reserve, leaving USD 7.75 unreserved.

## Remaining

- Protect this candidate-publication evidence through focused review and exact-main CI.
- Bind a fresh RC29 Azure/Snowflake correctness objective from the next protected main.
- Run one manual candidate and only its success-conditional replay, then clean up exactly.
- Record qualification, replay, cost, and cleanup evidence in a separate small PR.
- Complete remaining Phase 8 provider, scale, pairwise, soak, audit, and closure gates.

## Review First

- `docs/evidence/phase8/2026-08-17/rc29-candidate.json`
- `tickets/DANDER-216-private-rc29-candidate.md`
- `docs/cloud-portability-phase8-qualification.md`
