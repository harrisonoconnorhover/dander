# OCI Container Instances lifecycle acceptance

Status: accepted on 2026-08-13; public candidate qualified, schedule returned inactive, no live
Container Instances remain, and both OCI and retained GCP finished at no drift.

This proof qualifies only the named OCI Container Instances, PostgreSQL warehouse, PostgreSQL
state, no-catalog, and OCI Vault composition. It does not qualify BigQuery or another GCP service
from OCI, OKE, another warehouse, another state backend, or another Vault shape. OCI remains
experimental pending Phase 8 scale, cost, pairwise-profile, soak, and release qualification.

## Approval boundary

The operator approved repeated bounded Phase 7 attempts under a six-dollar per-attempt ceiling,
including public candidate publication, provider registration, disposable resources, live tests,
and cleanup. No automatic paid rerun was used without that standing ceiling.

Credentials, secret values, database connection strings, warehouse rows, Terraform state, and
binary plans stayed outside the repository. The committed evidence contains only public artifact
identity, normalized counts and states, run identifiers, and pass/fail outcomes.

## Accepted artifacts

- Public `dander-platform==0.9.0rc17` was tagged from protected-main commit
  `018d4722e1453c02fc676809679891b54e5e145e`. The public wheel SHA-256 is
  `a5e5c7bf17e58243684bf208ac0cb84ef68122d92e9a071da83c380d2ee4129d`.
- GAR and OCIR contained one equal source-free OCI index,
  `sha256:190e9caa082efcd72e9a2a586c082c266e48f99a0bb69b99e30114e3c8c886b9`, copied without
  rebuilding. Its `linux/amd64` manifest is
  `sha256:cdd37fdc8d12678a9ed1473abb28ccdf05bd4d6bc13f2fcc84533ebc9ba5c69d`; its
  `linux/arm64` manifest is
  `sha256:8eb0873e3d641f78f2d4b7ebffc0c70b921d445d17ebd004cd285d8130fde234`.
- The source-free controller build used only that reviewed wheel and produced the reviewed
  `linux/amd64` digest
  `sha256:ce027f189623e73f541df831d3abb4e122fa91971caa8d190c9284ddb98be0dd`.
- The runtime index retained its attached SBOM and provenance. Container Instances consumed an
  exact digest. OCI Functions consumed the matching wheel-bound tag and digest because that API
  requires a tag.

## Live lifecycle result

1. Short-lived OCI `SecurityToken` authentication passed preflight. The native Object Storage
   state bootstrap and platform foundation were reviewed and applied independently. Their final
   read-only verification reported exact no-change plans.
2. The public RC17 wheel installed outside the checkout with the `oci` extra. Its source-free
   generated project and immutable artifacts passed local contracts and protected-main CI before
   live use.
3. Run `oci-256d5f90dc3f42bb0b2609a6` completed the canonical PostgreSQL profile on RC17 with one
   attempt, 17 extracted and affected rows, one model, and three passing assertions. No row or
   connection value was retained.
4. RC15 had already proved exact replay, maximum-parallelism-one overlap exclusion, active
   cancellation, bounded logs, and owned Container Instance deletion. RC17 retained those
   contracts and re-proved the complete successful profile.
5. OCI Resource Scheduler started RC16 run `oci-e48d5dcbc437525ba988e461`, which completed 17 rows,
   one model, and three assertions. The scheduler/IAM path was unchanged in RC17; the later RC17
   correction affected only launcher-attempt ledger resumption. The schedule was returned to
   `INACTIVE` after the proof.
6. A deterministic RC17 diagnostic used runtime index
   `sha256:cbb11133829fd73b34a458e598dc5f6c8de96175038e74f53de33da89e91b7c9`.
   Run `oci-9c7f7f98a58f70d9e7fb283d` created a different Container Instance for each of its two
   whole-task attempts, preserved one logical run identity, and terminated after retry exhaustion
   with exit code 75 and `launcher_retry_exhausted`. This live-proved the protected PR #248
   run-ledger correction without changing the canonical runtime.
7. A new version of the existing versionless Vault application secret became `CURRENT`. The prior
   database credential was rejected, the current credential was accepted, and a new RC17 run
   observed it without an image rebuild or secret output. Vault contains no Terraform-managed
   secret value.
8. The controller and foundation were rolled back to exact public RC16 artifacts. Run
   `oci-2a83a1460a9edcdbecbc4007` completed 17 rows, one model, and three assertions. RC17 was then
   restored without rebuilding, and run `oci-df84f2ebdefaefd793fe7282` repeated the successful
   result.
9. The enabled error alarm targeted the reviewed OCI Notifications topic. No external topic
   subscription existed, so this proves alarm-to-topic routing only—not email or external delivery.
10. Direct OCI resource-principal federation to Google is unsupported: OCI's RPST is not a generic
    OIDC issuer/JWKS/audience contract that meets the existing refresh and revocation gate. Dander
    therefore rejects OCI-to-BigQuery rather than accepting a static Google key.
11. Final inventory found zero non-deleted Container Instances. The schedule remained inactive,
    RC17 remained deployed, and both OCI Terraform roots reported no drift. Fresh retained-GCP
    stage-zero and platform plans also reported exact `No changes.`; no GCP apply occurred.

## Provider limitations retained honestly

The bounded-cost default Vault does not provide automatic master-key rotation, and the live
Ashburn tenancy rejected OCIR repository tag immutability. Dander keeps manual key-version rotation,
versionless application-secret refresh, private repositories, mismatch rejection, digest
read-back, and digest-qualified task deployment. These are explicit provider limitations, not
false parity with other clouds.

## Verdict

The Phase 7 exit gate passes. The same public RC17 release digest passed OCI launcher conformance
and the complete named OCI profile. Schedule, retry, fencing, interruption, replay, rotation,
rollback, cleanup, alert routing, and no-drift evidence are present, while unsupported cross-cloud
identity is explicitly rejected. Phase 8 remains required before any OCI support promotion.
