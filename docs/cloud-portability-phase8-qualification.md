# Phase 8 qualification baseline

This is the authoritative starting inventory for Phase 8. It records accepted foundations and
open release gates without claiming scale, cost, soak, pairwise, or support qualification.
Protected `main` at `536b31b701a67a5b7eeb68e09e1d87a4c59898f9` passed complete CI after the
Phase 7 evidence merge.

## Accepted foundations

| Boundary | Current evidence | Phase 8 boundary |
|---|---|---|
| GCP native | Exact private RC22 passed authenticated manual/replay and Scheduler execution on the retained Cloud Run/BigQuery/Dataplex/Secret Manager profile, followed by no drift | Provider-measured cost, scale reports, and the retained soak remain open |
| Fargate to GCP | Public `0.8.0rc8` passed manual/scheduled lifecycle, replay, interruption, alerts, rollback, cleanup, and no drift | Scale qualification remains open; this is not the AWS-native profile |
| Kubernetes portable | Exact private RC22 passed the local existing-cluster lifecycle plus normalized correctness/bulk/incremental/transform/failure Jobs, including alert visibility and cleanup | Hosted-provider proof, remaining launcher classes/cost, and soak remain open |
| Azure canonical | The Snowflake/PostgreSQL/Key-Vault lifecycle passed; the separate BigQuery/GCP identity profile passed refresh and revocation | Exact-candidate scale, cost, pairwise, and soak remain open |
| OCI canonical | Public `0.9.0rc17` passed the complete PostgreSQL/OCI-Vault lifecycle on one digest | Exact-candidate scale, cost, pairwise, and soak remain open |
| Warehouses | BigQuery, PostgreSQL, Snowflake, and Redshift produced equal normalized common-scalar rows; exact RC22 passed seven local PostgreSQL classes, while private local RC23 observed equal DIRECT/COPY rows | Review invalidated RC23's byte-threshold objective; hosted PostgreSQL cost, final-candidate reruns, and all exact-candidate BigQuery, Snowflake, and Redshift scale reports remain open |
| Audits | Exact RC22 passed protected CI and the final-candidate repeat; qualification-baseline head `3ea34e2` passed all five jobs in run `31876449299`, and focused thirteenth review accepted that delta | Fourteenth review found two EC2 authorization blockers; correction `b9735c9` is local and pending protected CI/review before one source-free multi-platform replacement candidate |

## Open gates and dependency order

1. Completed: publish the normalized report contract and keep historical partial reports
   `not_evaluated`.
2. Completed on 2026-08-14: sanitized failure diagnostics merged and exact RC21 retained execution
   `dander-servicenow-incidents-7kxl4` emitted bounded class chains and a numeric status without
   messages, bodies, credentials, DSNs, or rows. This proves safe causal identity, not the external
   ServiceNow root cause.
3. Reopened after live preflight: the original AWS-native Fargate/Redshift/PostgreSQL/Glue/
   AWS-Secrets profile passed protected review, but exact RC22 did not package its selected AWS
   deployment. Successive reviews found and closed its projection, network, account, database-role,
   and legacy-manifest gaps. Head `34d6d55` passed protected run `31868849725`; the next review found
   the AWS source schema and Glue cleanup ownership still invalid. Commit `533125a` corrected both;
   protected CI and exact-head review then found unsupported model materialization, stale RC22
   Terraform identity, and a provisioned Redshift validation gap. Commit `9c6e27b` corrected all
   three and passed protected run `31871007170`; ninth review then found COPY-role trust,
   deployment-role authority, RDS-name, and VPC-range gaps. Commit `b031403` corrected those four;
   exact head `4c82438` passed protected run `31873024315`, and tenth review accepted them before
   finding Redshift create dependencies, Glue tag lifecycle, and fractional usage-limit gaps.
   Commit `7a1f429` corrected those three; head `d644b2a` passed protected run `31874238906`, and
   eleventh review accepted them before finding Data API credential and residual S3 cleanup gaps.
   Commit `ef18330` corrected both; head `67ab738` passed run `31875414186`, but twelfth review
   found forced cleanup also required version deletion. Commit `06ec187` added that exact action;
   current-main integration head `3ea34e2` passed run `31876449299`, and focused thirteenth review
   accepted the complete delta. Exact reconciliation head `0c65e42` passed run `31877158743`, but
   fourteenth review found security-group creation and unrestricted EC2 tag-ownership blockers.
   Commit `b9735c9` corrects both locally; protected CI and focused rereview remain open.
4. Completed as a baseline: protected private RC22 was cut after those merges and used for the GCP,
   local Kubernetes, and seven-class PostgreSQL records.
5. In progress: the post-RC22 bounded direct-write change is packaged as private arm64 RC23. Its
   local PostgreSQL run observed equal rows and both transports, but completion review invalidated
   the 1,400-byte recommendation and found lookahead inside the transaction. Both corrections passed
   protected review; the source distribution then omitted both Phase 8 harnesses. Commit `533125a`
   restored them and passed protected review; the eighth correction and a source-free
   multi-platform successor remain required.
6. Use one protected exact candidate for every remaining scale, cost, pairwise, canonical-profile,
   Kubernetes, and soak gate; then repeat the full audit and freeze the compatibility documents.

The operator approved cloud mutations, conservative provider-specific SLO selection, and an
aggregate Phase 8 ceiling of USD 10 on 2026-08-14. Private RC22 publication, the retained GCP
diagnostic, and private arm64 RC23 publication use pre-recorded USD 0.75, USD 1.25, and USD 0.25
allocations; provider-measured charges have not posted, so no exact cloud cost is claimed. Each paid
run must still record its objective manifest and per-run allocation before mutation, preserve the
dependency order, and use the immutable candidate.

Private `0.9.0rc22` at protected main `aebecade458e85c5d3b077c1f2a96ccd6ee825aa` remains the
protected exact candidate for its existing qualification records. Its source-free multi-platform index is
`sha256:ce395dda3865691d2300f57577fb9b5297031293f77c89f6adc34f60853947c3`; its packaged GCP
and Kubernetes deployments passed read-only runtime inspection, but no AWS deployment is present.
Private local `0.9.0rc23` at
`2455fc34d4503863060b7bac873be36319c13e4f` adds the bounded direct path and is published only as
an arm64 qualification image at index
`sha256:8bd35188dbdb09bb33be7132a7681577249677e4b3c8a0e76ede4a2975733064`. It is not protected,
multi-platform, or a support candidate, and RC22 reports do not transfer to it. Public RC20 remains
unchanged.

## Pre-candidate release readiness

Commit `2d020d15fc52` passed a local release-readiness audit on 2026-08-14: wheel and source archive
inspection, clean installation and generated-project validation for both artifacts, the full
multi-provider runtime import, dependency audit, all Terraform format/validation and mocked module
tests, Helm lint/render, the main container runtime contract, and the OCI controller container
contract. Trivy configuration/image scans, the Git-history secret scan, protected CI, and the
independent completion review were not available locally. This preflight does not substitute for
repeating every audit on the final source-free candidate.

## Final-candidate release audit

Exact private RC22 at `aebecade458e85c5d3b077c1f2a96ccd6ee825aa` passed protected CI run
`31825533602`: Python quality, secret scan, Terraform quality, distribution install, and container
build/scan all succeeded. The local repeat verified the exact wheel and source archive from clean
installs, the full runtime import, generated-project Terraform, all provider Terraform roots and
mocked module tests, both Helm charts, rootless read-only execution, and the main and OCI-controller
images. Pip-audit found no known dependency vulnerability; workflow-pinned Trivy found no
HIGH/CRITICAL infrastructure or image finding; workflow-pinned Gitleaks found no leak across 453
commits. The post-merge regression suite passed 1,702 tests with 28 skips. See
`docs/evidence/phase8/2026-08-14/rc22-local-audit.json`.

Private arm64 RC23 separately passed its local preflight: exact wheel/source inspection and clean
runtime-all installs, rootless read-only runtime execution, release/control metadata checks,
1,708 tests with 34 skips, dependency and exact-source secret audits, and zero HIGH/CRITICAL Trivy
findings in the image or infrastructure. This does not substitute for protected CI or the final
multi-platform candidate audit. See `docs/evidence/phase8/2026-08-14/rc23-local-audit.json`.

## Normalized report contract

`io.dander.qualification.report/v1` records the exact release version, full commit, immutable image
digest, date, named profile, launcher, warehouse, state, catalog, secret provider, regions, service
shapes, provider job IDs, approved cost ceiling, deterministic workload shape, common performance
measurements, provider metrics, costs, and objective results.

Every passed report also embeds one independently approved, exact objective-name set and its stable
approval reference, bound to the benchmark class, profile, release, commit, image digest, and
workload-configuration hash. Omitting or adding an objective—or reusing a set approved for another
context—cannot silently turn a partial benchmark into qualification evidence.

Common measurements use `measured` or `unavailable`; an unavailable metric is never serialized as
zero, and a boolean cannot be accepted as numeric evidence. Report status is separately
`not_evaluated`, `failed`, or `passed`. A passed report requires every common metric, explicit USD
cost evidence including an honestly measured zero, and every approved objective to pass.
Bounded-memory reports additionally require an externally enforced limit, logical input at least
ten times that limit, and peak RSS no greater than 80 percent.

## Deterministic profile and pairwise matrix

| Case | Launcher | Warehouse | State | Catalog | Secret | Current status |
|---|---|---|---|---|---|---|
| `gcp_native` | Cloud Run | BigQuery | BigQuery | Dataplex | GCP Secret Manager | exact-candidate profile rerun passed; cost and soak open |
| `aws_native` | Fargate | Redshift | PostgreSQL | Glue | AWS Secrets Manager | EC2 authorization correction `b9735c9` awaits protected CI/review; replacement candidate and live qualification remain open |
| `kubernetes_portable` | Kubernetes | PostgreSQL | PostgreSQL | none | environment projection | local lifecycle accepted; Phase 8 live proof open |
| `azure_snowflake` | Azure Container Apps Jobs | Snowflake | PostgreSQL | none | Azure Key Vault | lifecycle accepted; Phase 8 open |
| `oci_native` | OCI Container Instances | PostgreSQL | PostgreSQL | none | OCI Vault | lifecycle accepted; Phase 8 open |
| `fargate_gcp` | Fargate | BigQuery | BigQuery | Dataplex | GCP Secret Manager | lifecycle accepted; Phase 8 open |
| `azure_gcp` | Azure Container Apps Jobs | BigQuery | BigQuery | Dataplex | GCP Secret Manager | identity/lifecycle accepted; Phase 8 open |
| `kubernetes_gcp` | Kubernetes | BigQuery | BigQuery | Dataplex | GCP Secret Manager | Phase 8 live proof open |
| `oci_gcp` | OCI Container Instances | BigQuery | BigQuery | Dataplex | GCP Secret Manager | unsupported; resource-principal identity does not meet the refresh contract |

These cases cover every first-class launcher, warehouse, state backend, catalog mode, launcher
secret path, and the roadmap's named cross-cloud boundaries without promising an unsupported
Cartesian product. A case cannot change to supported from adapter tests alone.

## Current provider blockers

The 2026-08-14 read-only credential preflight found Azure blocked on required interactive Entra
reauthentication, AWS blocked on an expired session, and OCI blocked on an incomplete CLI profile.
AWS access was subsequently restored. Exact RC22 was copied byte-identically to private ECR, and
the pre-approved 28-resource disposable data plane was created within its USD 3 allocation. A
read-only inspection then found that the image packages only GCP and Kubernetes deployments, so no
Fargate plan, task, or pipeline execution was attempted. The exact 28-resource destroy completed,
the qualification state and inventories contain zero managed data-plane resources, and the existing
AWS D7 lane was unchanged. Provider charges have not posted, so the profile cost and correctness
remain `not_evaluated`. Completion review later found the disposable task group also lacked
self-scoped database egress. Later corrections reached protected head `34d6d55` and passed run
`31868849725`; exact-head review then found an incompatible nested Redshift source and unowned Glue
cleanup. Commit `533125a` passed protected CI and review at head `3eed46e`; that review found the
remaining materialization, candidate-identity, and provisioned-role validation gaps. Commit
`9c6e27b` passed protected CI and review at `0b1a8fa`; ninth review found the remaining COPY trust,
deployment authority, name-validation, and CIDR-validation gaps. Commit `b031403` corrected those;
protected head `4c82438` passed run `31873024315`, and tenth review accepted them before finding
Redshift create dependencies, Glue tagging/refresh, and fractional usage-limit gaps. Commit
`7a1f429` corrected those; head `d644b2a` passed protected run `31874238906`, and eleventh review
accepted them before finding Data API credential and residual S3 cleanup gaps. Commit `ef18330`
corrected both, and head `67ab738` passed run `31875414186`; twelfth review then found that forced
cleanup also required `s3:DeleteObjectVersion`. Commit `06ec187` added that exact permission.
Current-main integration head `3ea34e2` passed all five jobs in run `31876449299`, and focused
thirteenth review accepted the delta. Reconciliation head `0c65e42` passed all five jobs in run
`31877158743`; fourteenth review then found two EC2 authorization blockers now corrected locally in
`b9735c9`. Protected CI and focused rereview remain open. Interactive
Azure and OCI authentication was subsequently restored and
verified through provider APIs. Azure has zero Dander-named resources. OCI retains the accepted
Phase 7 foundation and private image history with zero active Container Instances; that retained
no-drift baseline must be preserved. Credentials no longer block either provider, but the protected
replacement-candidate gate still blocks new qualification runs and none of these cases inherits a
support claim. Sanitized details are in
`docs/evidence/phase8/2026-08-14/provider-credential-blockers.json` and
the three provider credential-restoration records beside it.

## Current PostgreSQL scale evidence

Exact RC22 ran inside its immutable source-free image against disposable TLS PostgreSQL 15.18.
The approved correctness fixture produced its exact normalized SHA-256 before and after replay.
The approved bulk workload passed 500,000 narrow rows at 38,681.727 rows/second and 200,000 wide
rows at 9,032.608 rows/second. The approved incremental workload applied a 3,000-row delta to a
300,000-row seed at 16,483.516 rows/second, finished with the exact 301,500-row target, and rejected
cursor regression. Both schemas and all temporary staging relations were removed; local measured
service cost was USD 0.

The transform class scanned 100,000 facts, joined 100 dimensions, produced exact ten-category
aggregates, applied an update plus insert through the incremental model, and passed 21 generic
assertion executions. Its final target contained 100,001 rows, all schemas/staging were removed,
and local measured service cost was USD 0. The first attempt stopped before candidate transform
code on a harness SQL-escaping defect; the corrected retry and both cleanup checks are retained.

The PostgreSQL-specific failure class bounded pool exhaustion at 104 ms, replaced a terminated
state connection and recovered its watermark operation, cancelled a warehouse query, and verified
the enclosing transaction rolled back. All six approved objectives passed in 173 ms with exact
cleanup and USD 0 local cost. Connector throttling/credential expiry and catalog/process/launcher
failures remain in their respective connector and launcher profile gates.

RC22's PostgreSQL writer factory exposes only COPY, so its record cannot satisfy crossover. Private
local RC23 ran the pre-approved paired row/byte workload against TLS PostgreSQL 15.18. Across five
repetitions at 1, 10, 100, 1,000, and 5,000 rows, COPY and DIRECT produced equal canonical rows and
emitted their selected transports; DIRECT tied COPY only at 10 rows. Completion review found that
the recorded 1,400-byte recommendation omitted field-name bytes counted by the writer, so that
threshold would select COPY and its `threshold_recorded` objective is invalid. The corrected
harness derives 1,490 bytes from the writer's exact normalized logical-size function, and bounded
lookahead now completes before a transaction opens. The RC23 report is not rewritten or promoted;
the replacement candidate must rerun crossover. Global defaults remain disabled.

## Current Kubernetes scale evidence

One exact-RC22 kind 1.32.2 Job ran the same five normalized classes under a 2 CPU/512 MiB limit,
600-second deadline, and zero launcher retries against TLS PostgreSQL 15.18. Correctness, bulk,
incremental, transform, and PostgreSQL-specific failure all passed; bulk processed 700,000 rows in
19.206 seconds, and all reports record `launcher=kubernetes`, profile `kubernetes_portable`, exact
candidate identity, USD 0 local cost, and the reviewed objectives. PostgreSQL retained no Dander
schema or staging relation. The namespace, Secrets, TLS material, cluster, and temporary tags were
deleted with zero Warning events.

The first successful Job wrote reports only to its completed Pod's ephemeral volume; a second
unchanged workload added a reporter sidecar and retained all five reports. Both successes and the
collection limitation are preserved in the attempts ledger. This is a local launcher-scale slice,
not hosted-provider scale, crossover, distinct cost-class, or soak evidence.

## Current exit recommendation

Phase 8 remains open. The safe diagnostic gate, RC22 Kubernetes/GCP records and seven local
PostgreSQL classes, RC22 protected audit, RC23 local preflight/transport observation, and exact AWS
cleanup evidence are complete. Qualification-baseline head `3ea34e2` passed protected run
`31876449299`, and focused thirteenth review accepted the final version-cleanup correction plus
current-main integration. Reconciliation head `0c65e42` also passed protected CI, but fourteenth
review found two EC2 authorization blockers; correction `b9735c9` awaits protected CI and focused
rereview. After PR #291 merges, each replacement-candidate, benchmark, provider,
optimization, or live-defect lane starts from fresh protected `main`; rerun only materially affected
evidence plus the eventual final-candidate closure matrix. Remaining work includes rerunning
applicable RC22 reports on that one candidate; PostgreSQL hosted cost; remaining
benchmark classes/providers and Kubernetes hosted scale/soak; hosted-provider and pairwise live
proofs; scale/cost reports for every first-class warehouse and launcher; remaining canonical-profile
evidence; release-candidate soak; profile operator docs; and the frozen support matrix.
