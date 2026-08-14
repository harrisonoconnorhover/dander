# PostgreSQL on Kubernetes lifecycle acceptance

Accepted locally on 2026-08-10 against commit
`a9804ac0f8198923af9bb50512f688034e4501ee`. This is existing-cluster lifecycle evidence for
the experimental PostgreSQL/Kubernetes profile. It is not hosted-provider, scale, reliability,
alerting, or support qualification.

## Artifact and environment

- A source-free image was built from an unpublished local `dander-platform==0.8.0rc8` wheel. The
  wheel SHA-256 was `8ac41a105a7539bf3feda117a533a4a2495b43c9b446f64c479943ea3e27c3f2`;
  the image contained generated project files and no `src/` directory.
- The non-root image user was `65532:65532`. The local-registry image digest was
  `sha256:9e7a88784ecb160cff1158409c6b3f4feb5ae8f88c7baf71b7f7e8cd53a09539`,
  and its revision label matched the tested commit.
- The disposable environment used kind 0.32.0, Kubernetes client/server 1.32.2, Helm 4.2.3,
  Docker 28.3.0, and PostgreSQL 15.18 at
  `sha256:3d0f7584ed7d04e27fa050d6683a74746608faf21f202be78460d679cc56461f`.
- PostgreSQL used an ephemeral self-signed TLS certificate. Dander rejected the initial
  unencrypted fixture, preserving its TLS-required provider boundary.

## Plan and execution

- `dander kubernetes plan` linted the packaged chart and saved reviewed values and manifests.
  The final image-only revision changed exactly the image reference and projected digest.
- Helm installed one paused CronJob plus its ServiceAccount and ConfigMap. The operator-managed
  PostgreSQL DSN Secret remained outside Helm ownership. `dander kubernetes verify` matched the
  context, namespace, pipeline, schedule, and immutable image before and after rollback.
- The initial run and inclusive replay each extracted two rows, built one model, passed two
  assertions, and published one metadata asset without duplicates or cursor regression.
- An update run captured a changed row and a third row. The final committed-image smoke retained
  three distinct raw rows and three distinct model rows, passed both assertions, and advanced the
  watermark to `2026-08-10T12:03:00+00:00`.
- PostgreSQL retained four successful run-ledger records. The lease released after every accepted
  run, its fencing token reached 4, raw and model target commits were committed, and no staging
  relation remained.

## Schedule, rollback, and cleanup

- A reviewed Helm revision changed the paused `0 9 * * *` schedule to enabled `30 23 * * *`.
  `helm rollback` restored the original paused schedule, and the final image revision kept it
  paused. Installed values were semantically identical to the final reviewed plan.
- Helm uninstall removed all Helm-owned Dander resources. The fixture namespace, PostgreSQL,
  local registry, and kind cluster were then deleted; `kind get clusters` returned none.
- No GCP project, retained deployment, public package, or other cloud resource was changed.

## Result

The local existing-cluster lifecycle gate passes for Kubernetes plus PostgreSQL state, ingestion,
transforms, assertions, metadata, replay, fencing, scheduling, rollback, and cleanup. Controlled
overlap, process interruption, alerts, hosted Kubernetes, and scale qualification were not
evaluated here and remain separate gates. PostgreSQL/Kubernetes remains experimental.

Before review, fresh read-only plans against the retained GCP deployment's authoritative
source-free bundle reported exactly `No changes.` for both stage zero and the platform. An older,
superseded retained-project copy was not changed or used to manufacture that result. No Terraform
apply or cloud mutation occurred.

## Phase 8 exact-candidate lifecycle rerun

On 2026-08-14, private `0.9.0rc22` index `sha256:ce395d…47c3` passed the contract-valid
correctness and failure objective sets on a disposable kind 1.32.2 cluster. The source-free image
completed manual, replay, CronJob-scheduled, rotated-Secret, RC21 rollback, and RC22 restoration runs.
One simultaneous run succeeded while its peer recorded a truthful skip; a controlled interruption
exited 130, a ten-second hard deadline emitted `DeadlineExceeded`, and a live operator-owned event
watch received the Warning. Row/key counts remained 16/16, no lease or staging relation remained,
Helm preserved the operator-owned Secret and PostgreSQL deployment, and final namespace/cluster
cleanup passed.

This exact-candidate lifecycle evidence does not establish hosted Kubernetes, scale/cost, soak, or
support qualification. The profile remains experimental; see
`docs/evidence/phase8/2026-08-14/kubernetes-lifecycle.json`.
