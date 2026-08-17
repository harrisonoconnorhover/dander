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
| Kubernetes portable | Exact private RC27 passed the named local kind profile and one hosted GKE bounded-memory audit's non-cost objectives; the accepted lifecycle evidence remains current | Provider-posted GKE cost, other hosted-provider scale/cost, remaining launcher classes, and soak remain open |
| Azure canonical | Exact private RC29 passed fresh Container Apps/Snowflake/PostgreSQL/Key-Vault correctness and replay with zero retries and cleanup inside 120 minutes; the separate BigQuery/GCP identity profile passed refresh and revocation | Exact-candidate scale, provider-posted cost, pairwise, and soak remain open |
| OCI canonical | Public `0.9.0rc17` passed the complete PostgreSQL/OCI-Vault lifecycle on one digest | Exact-candidate scale, cost, pairwise, and soak remain open |
| Warehouses | BigQuery, PostgreSQL, Snowflake, and Redshift produced equal normalized common-scalar rows; exact RC27 passed local PostgreSQL classes plus the GKE bounded-memory non-cost objectives | Hosted PostgreSQL cost, applicable final-candidate reruns, and all exact-candidate BigQuery, Snowflake, and Redshift scale reports remain open |
| Audits | Exact RC22 passed protected CI and its historical final-candidate repeat; private RC24 publication evidence merged in PR #299 and exact-main CI run `31884123337` passed all five jobs | RC24's final-candidate repeat remains open |

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
   Commit `b9735c9` corrects both; correction/current-main head `d8a18ec` passed protected run
   `31878215886`, and focused fifteenth review accepted the correction. Docs-closure head `6ede9da`
   passed run `31879161660`, but sixteenth review found that route-table, subnet, and VPC-endpoint
   creation still lacked their existing-resource dimensions. Commit `e12ee59` adds the tagged VPC
   and route-table dependency grants; correction/docs head `0da600b` passed run `31879898267`, and
   focused seventeenth review accepted the correction. PR #291 then merged the complete baseline
   as protected-main commit `3d7783c`; exact-main CI run `31882061192` passed all five jobs.
4. Completed as a baseline: protected private RC22 was cut after those merges and used for the GCP,
   local Kubernetes, and seven-class PostgreSQL records.
5. Completed candidate publication: the post-RC22 bounded direct-write change was first packaged
   as private arm64 RC23. Its
   local PostgreSQL run observed equal rows and both transports, but completion review invalidated
   the 1,400-byte recommendation and found lookahead inside the transaction. Both corrections passed
   protected review; the source distribution then omitted both Phase 8 harnesses. Commit `533125a`
   restored them and passed protected review. Fresh-main PR #298 merged private RC24 as protected
   main `c19de3980411f20514326db9f722f07e57a3d1ef`; exact-main run `31882919709` passed all five
   jobs. The exact wheel then produced source-free amd64/arm64 index `sha256:b7eadc7e…9488` with
   SBOM and provenance. PR #299 merged its sanitized publication evidence as protected-main commit
   `a66ce65`; exact-main CI run `31884123337` passed all five jobs.
6. Completed locally; protected review remains: RC24 passed the corrected PostgreSQL crossover
   objective set. COPY and DIRECT produced equal canonical rows and emitted selected transports,
   but DIRECT lost at the first sampled size, so no contiguous DIRECT-winning prefix exists and
   the measured recommendation remains disabled at zero.
7. Use one protected exact candidate for every remaining scale, cost, pairwise, canonical-profile,
   Kubernetes, and soak gate; then repeat the full audit and freeze the compatibility documents.

The operator approved cloud mutations, conservative provider-specific SLO selection, and an
aggregate Phase 8 ceiling of USD 10 on 2026-08-14. Private RC22 publication, the retained GCP
diagnostic, private arm64 RC23 publication, private RC24 publication, and the combined
RC25/RC26/RC27/RC28
replacement publications use pre-recorded USD 0.75, USD 1.25, USD 0.25, USD 0.25, and USD 0.25
allocations. RC25 through RC28 share the final publication allocation, leaving USD 0 unallocated;
provider-measured charges have not fully posted, so no exact cloud cost is claimed. RC28 may publish
under that cumulative allocation only after its preparation merges and exact-main CI passes.
Each paid run must still record its objective manifest and per-run allocation before mutation,
preserve the dependency order, and use the immutable candidate.

A read-only cost reconciliation on 2026-08-16 found no basis for a new paid objective. AWS Cost
Explorer still denies `ce:GetCostAndUsage` to the operator, Azure Cost Management returned no
posted rows for August 14 through 16, and the retained GCP billing report still showed no GKE or
Compute Engine charge for the hosted run. Its displayed August 14/15/16 subtotals were USD
0.00/0.24/0.02, while the USD 1.31 month-to-date subtotal includes activity before the Phase 8
authorization window and cannot be attributed wholly to this phase. Provider-measured aggregate
spend and exact remaining headroom therefore remain unknown, the USD 0 unallocated contingency is
unchanged, and every affected cost objective stays `not_evaluated`. No cloud mutation or support
promotion follows from this observation; see
`docs/evidence/phase8/2026-08-16/provider-cost-reconciliation.json`.

## Canonical qualification harness invocation

Future qualification manifests use the immutable image's normal `dander` entrypoint and pass the
operator-mounted harness through the stable command:

```text
dander qualification-run /qualification/harness.py [HARNESS_ARGUMENTS...]
```

Kubernetes Jobs express that command as container `args`; they do not override `command` with
`/app/.venv/bin/python`, `/usr/local/bin/python`, or another image-layout path. The command validates
that the trusted, read-only mounted harness is a readable file, then replaces itself with the
runtime's installed interpreter while forwarding arguments and exit status. It does not select
objectives, grant provider access, or make an unreviewed harness trusted.

This current-source rail responds to two RC27 infrastructure preflights that stopped before Python
started. It does not rewrite or invalidate their attempt ledgers, transfer results to a later
candidate, or require unaffected accepted evidence to rerun. RC27 predates the command; a later
candidate and only materially affected qualification lanes will consume it.

## Interactive preflight and resource lifetime

Every user-interactive provider session and account-selection check must pass before the first
owned disposable resource starts the objective's lifetime clock. If role-scoped authorization
requires named setup, create only that minimum setup, obtain the authorization, and do not begin
the remaining paid infrastructure until it is available.

After the lifetime clock starts, an interactive-user blocker ends the attempt: preserve the
preflight state, start exact cleanup, and record the failure. Do not leave paid disposable
infrastructure running while waiting for input. Every such objective must set a cleanup-start
deadline early enough to leave explicit provider-deletion margin inside its maximum lifetime.

## Azure immutable runtime profile projection

Azure qualification planning projects the exact selected deployment into the source-free image
through the existing validated `DANDER_PLATFORMS_CONFIG_JSON` runtime boundary. The pipeline-scoped
document contains provider coordinates, runtime limits, schedule, resource names, and secret
references, but never Key Vault values. `dander runtime execute` materializes it in writable
scratch space and selects the deployment name passed by the Container Apps Job.

This current-source correction closes the pre-live configuration handoff only. It does not qualify
Azure, transfer Phase 6 results to a later candidate, create resources, consume a Phase 8 objective,
or authorize an uncommitted benchmark. Azure exact-candidate scale, cost, pairwise, and soak remain
open.

PR #351 prepared private `0.9.0rc28`; its exact protected-main commit `7135b8c` passed all five jobs
in run `31961210116` before one source-free amd64/arm64 publication. The resulting private GAR index
is `sha256:f825927627d3e4e996fcb885338cef50ced290284231bb7b5ed47d0f0f94959e`.
Both architectures passed exact-wheel, rootless read-only conformance, and stable
qualification-entrypoint probes. GCP, Kubernetes, externally projected AWS, and externally
projected Azure selectors passed without provider access; SPDX SBOM and SLSA provenance are
attached. The Azure selector deliberately maps deployment `azure_container_apps` to platform
`azure_snowflake`, proving the corrected deployment-to-platform handoff. This is candidate
publication evidence only: qualification, provider cost, public release, and support remain open,
and a separately protected exact-candidate objective must precede Azure mutation. The focused RC28
Azure objective now fixes one manual run and one success-conditional replay on the canonical
Container Apps/Snowflake/PostgreSQL/no-catalog/Key-Vault profile, the packaged synthetic fixture,
the disposable provider resources, a USD 2 ceiling, and exact cleanup. It authorizes nothing until
protected review and exact-main CI pass; see
`docs/evidence/phase8/2026-08-16/azure-snowflake-rc28-correctness-objectives.json` and
`docs/evidence/phase8/2026-08-16/rc28-candidate.json`.

PR #353 merged that objective as protected main `fdcf14d`; exact-main run `31964559562` passed all
five jobs before provider mutation. One manual execution then reached Python and Snowflake on the
exact ACR digest, but failed closed before writing rows because the runtime role lacked
database-level `CREATE SCHEMA`, which the writer requires for its owned staging-schema lifecycle.
The manual allowance was consumed, so the success-conditional replay and corrective rerun did not
start. Reviewed destroy plans removed 7 platform, 6 network/PostgreSQL, and 6 stage-zero resources;
the named Snowflake objects and active Azure inventories are empty, with only the expected inactive
purge-protected Key Vault tombstone remaining. Cost Management had no posted rows, so cost stays
`not_evaluated`. RC28 publication evidence remains valid, but Azure correctness and support remain
open. PR #355 closed DANDER-213 as protected main `4815561`; exact-main run `31973943176` passed
all five jobs. The mandatory canonical preflight now requires the exact runtime role's
database-level `CREATE SCHEMA` grant without creating a schema or exposing grant rows. A fresh
protected objective and known budget headroom remain mandatory before RC28 may run again; see
`docs/evidence/phase8/2026-08-16/azure-snowflake-rc28-correctness-attempt.json`.

The operator later granted a separate USD 10 additional-spend ceiling for the remaining Phase 8
work and directed the Azure lane to resume. AWS Cost Explorer access now passes with a posted
baseline that rounds to USD 0.00; Azure ActualCost access passes but still returns no rows; the
latest retained GCP billing observation predates the incremental authorization. The fresh RC28
Azure retry objective reserves its full USD 2 ceiling as the conservative bound while invoices
lag, leaving at least USD 8 for later objectives. It uses a new disposable provider namespace,
requires the narrow database-level `CREATE SCHEMA` grant before token issue, and requires the
corrected canonical preflight before its one manual run and success-conditional replay. Prior
failure results do not transfer. No mutation may begin until this objective passes protected review
and exact-main CI; see
`docs/evidence/phase8/2026-08-16/azure-snowflake-rc28-correctness-retry-objectives.json`.

PR #358 merged the retry objective as protected main `c4ad281`; exact-main run `31981210288`
passed all five jobs before mutation. The new namespace passed PostgreSQL TLS, narrow Snowflake
grant, exact-image, secret-binding, zero-retry, and canonical preflight checks. Its one manual RC28
execution reached Python and Snowflake, then failed non-retryably with Snowflake error 904 before
any row write. Source metadata proves quoted lowercase `id` and `title` exist while uppercase `ID`
does not: RC28 renders portable logical columns and aliases unquoted, so Snowflake uppercases them.
This is a deterministic application defect, not a provider, credential, setup, or operator-tooling
failure. Automatic retry stayed disabled and the success-conditional replay did not run. Exact
cleanup removed all 19 active Azure resources and the named Snowflake objects; one subnet deletion
waited for Azure's scheduled environment deletion, then the exact two-resource recovery plan
finished. Only the expected inactive Key Vault tombstone remains. ActualCost still has no posted
row, so the full USD 2 conservative bound remains held and cost is `not_evaluated`. PR #360 merged
the focused correction as protected main `a2b72f8`; exact-main run `31987252875` passed all five
jobs. RC28 must not rerun; a replacement immutable candidate is next. See
`docs/evidence/phase8/2026-08-17/azure-snowflake-rc28-correctness-retry-attempt.json`.

PR #362 merged private RC29 preparation as protected main `7a6d138`; exact-main run `31988620430`
passed all five jobs before one source-free amd64/arm64 publication. Private GAR index
`sha256:e016419f…aad54` passed exact-wheel, dual-architecture rootless read-only, stable
qualification-entrypoint, SPDX SBOM, and SLSA provenance checks. The USD 0.25 reserve plus the
existing USD 2 delayed-billing bound leaves at least USD 7.75 unreserved. This is candidate
publication evidence only, not live qualification, provider cost, public release, or support
evidence. See `docs/evidence/phase8/2026-08-17/rc29-candidate.json`.

PR #364 merged the RC29 Azure correctness objective as protected main `46199fe`; exact-main run
`31991302574` passed all five jobs before mutation. One manual execution and its success-conditional
replay both passed with no retry. OAuth-role readback matched the approved normalized hash, left
three distinct raw and model rows, and proved one target commit after replay. Reviewed cleanup
removed all active named Snowflake and Azure resources; only the expected inactive Key Vault
tombstone remains. Qualification nevertheless failed because resource-group deletion began about
431 minutes after creation, beyond the committed 120-minute maximum. RC29 has no application
defect, but no result transfers: a fresh objective must front-load interactive readiness and enforce
an earlier cleanup deadline. ActualCost has posted USD 0.0024353794 for August 17, but delayed
attribution keeps cost `not_evaluated` and the USD 2 conservative bound held. See
`docs/evidence/phase8/2026-08-17/azure-snowflake-rc29-correctness-attempt.json`.

A fresh RC29 lifetime retry objective now binds the unchanged digest to new `r29c` Azure,
PostgreSQL, and Snowflake names. It verifies Snowflake's interactive session before the first owned
object, obtains the runtime-role token before Azure provisioning, and starts cleanup by minute 75
to preserve 45 minutes of deletion margin inside the unchanged 120-minute maximum. Any later
interactive blocker aborts the attempt and starts exact cleanup immediately. The new USD 2 bound
leaves USD 3.75 unreserved. This is an unprotected objective proposal only; no owned provider
resource may be created before protected review, merge, and exact-main CI. See
`docs/evidence/phase8/2026-08-17/azure-snowflake-rc29-correctness-lifetime-retry-objectives.json`.

PR #366 merged that objective as protected main `2b1597f`; exact-main run `32024585468` passed all
five jobs before the first owned resource. The unchanged RC29 digest then passed one fresh manual
Container Apps execution and its success-conditional replay with distinct run identities, three
written model rows and three successful model assertions per execution, and zero retries.
Signed-in Snowflake query history retained the exact write and assertion identities after cleanup.
Cleanup began 26.34 minutes after Snowflake created the first owned object; final Azure absence was
observed at 54.52 minutes, inside both the 60-minute target and 120-minute maximum. Terraform's
state-storage `prevent_destroy` guard remained enabled: exact targeted deletion plus disposable
resource-group removal preserved the guard, and refresh-only reconciliation left zero managed
stage-zero state. All active named Azure and Snowflake resources are absent; only the expected
inactive purge-protected Key Vault tombstone remains. ActualCost returned no row, so the full USD 2
bound remains held and exact provider cost is pending. This closes the Azure canonical correctness
and lifecycle rerun only; scale, provider-posted cost, pairwise, soak, final closure, public release,
and support remain open. See
`docs/evidence/phase8/2026-08-17/azure-snowflake-rc29-correctness-lifetime-retry-attempt.json`.

Private `0.9.0rc22` at protected main `aebecade458e85c5d3b077c1f2a96ccd6ee825aa` remains the
protected exact candidate for its existing qualification records. Its source-free multi-platform index is
`sha256:ce395dda3865691d2300f57577fb9b5297031293f77c89f6adc34f60853947c3`; its packaged GCP
and Kubernetes deployments passed read-only runtime inspection, but no AWS deployment is present.
Private local `0.9.0rc23` at
`2455fc34d4503863060b7bac873be36319c13e4f` adds the bounded direct path and is published only as
an arm64 qualification image at index
`sha256:8bd35188dbdb09bb33be7132a7681577249677e4b3c8a0e76ede4a2975733064`. It is not protected,
multi-platform, or a support candidate, and RC22 reports do not transfer to it. Private
`0.9.0rc24` at protected main `c19de3980411f20514326db9f722f07e57a3d1ef` is the replacement
candidate at index
`sha256:b7eadc7e42eb5b6783685d22ce31711a1bc1a7ee40323bf41683e574f5839488`.
Both runnable manifests report RC24; the source-free image passed GCP, Kubernetes, and externally
projected AWS deployment selection plus credential-free read-only conformance. This is candidate
publication evidence plus one corrected local crossover result, not a transferred benchmark,
live-provider result, hosted-cost pass, or support claim. Public RC20 and the five retained RC22
jobs remain unchanged.

Private `0.9.0rc25` at protected main `f5935a6d263cf6734ee9944f2f0e02d025edc63e` is the
replacement candidate at index
`sha256:5a0d5520a2789cdf089015396f41047508a086cbc8ec87a9ded405d880dc2238`.
Exact-main run `31902553474` passed all five jobs. Both runnable manifests report RC25; the exact
wheel-built source-free image passed GCP, Kubernetes, and externally projected AWS deployment
selection plus credential-free read-only conformance, and both platform attestations contain SPDX
SBOM and SLSA provenance predicates. It packages the AWS-native Fargate identity correction, but
no RC24 report transfers to it. This is private candidate publication evidence, not a live-provider,
cost, benchmark, public-release, or support pass.

PR #318 merged the sanitized RC25 publication record as protected-main commit `ae3be54`; exact-main
CI run `31903775539` passed all five jobs. The first RC25 AWS correctness gate remains preserved at
`docs/evidence/phase8/2026-08-15/aws-native-rc25-profile-objectives.json`; its task reached a cold
Redshift Serverless start and failed at the configured 30-second connection timeout. PR #321 merged
that failed-attempt evidence as protected main `b784318`. The replacement gate is
`docs/evidence/phase8/2026-08-15/aws-native-rc25-profile-objectives-v2.json`: it retains the exact
candidate, objective set, USD 3 ceiling, one manual execution, one replay, paused scheduling, and
exact cleanup while binding a 120-second Redshift connection timeout under the unchanged 600-second
runtime deadline. PR #322 merged that gate as protected main `ea625e3`; exact-main CI run
`31911384116` passed all five jobs. PR #323 then merged the runbook correction as protected main
`c14c6fa`; exact-main run `31912057557` passed all five jobs. The replacement run connected to
Redshift and created its temporary table, then COPY failed because the runtime database role lacked
effective ASSUMEROLE permission on the explicit S3 staging role. Replay did not start. Exact cleanup
removed all 25 platform and 36 data-plane resources, both Terraform states and direct owned-resource
inventories are empty, and the attempt's KMS key is pending deletion on 2026-09-14. This is a
live-discovered candidate defect: at that point it required a focused implementation PR,
replacement candidate, and complete fresh objective. No result, cost, public-release, or support
claim transfers.

PR #324 merged the sanitized failure record as protected main `804496e`; exact-main CI run
`31914082961` passed all five jobs. PR #325 then merged the exact staging-role grant as protected
main `7cea5a8`; exact-main CI run `31914830354` passed all five jobs. PR #326 merged private
`0.9.0rc26` as protected main `f0fe54f`; exact-main CI run `31915564765` passed all five jobs. Its
exact wheel built private source-free multi-platform index `sha256:e63aef4b…d28e`. Both runnable
manifests report RC26; GCP, Kubernetes, and externally projected AWS deployment selection plus
rootless read-only conformance passed on both architectures, and both platform attestations contain
SPDX SBOM and SLSA provenance predicates. This is private candidate publication evidence, not a
live-provider, cost, benchmark, public-release, or support pass. RC25 AWS results do not transfer.
PR #327 merged the sanitized RC26 publication record as protected main `6e9d65e`; exact-main CI run
`31916736418` passed all five jobs. The fresh AWS correctness gate is
`docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives.json`: it binds one manual
execution and one replay to exact RC26, `us-east-1`, paused scheduling, exact cleanup, the existing
USD 3 allocation, and the reviewed 120-second Redshift connection timeout. No AWS mutation may
precede this objective commit's protected review and exact-main CI.

That protected objective was consumed by one exact RC26 manual task. The task ran the expected
digest and completed PostgreSQL state setup, then Redshift connection validation expired at 121,066
ms with zero provider operations or rows. The private workgroup was available and shared the task's
VPC, subnets, and security group; an immediate Data API read completed after failure, so the record
does not yet distinguish a cold wake-up from another connection-path delay. Replay did not start.
Exact saved-plan cleanup removed all 25 platform and 36 data-plane resources; both states and direct
active inventories are empty, and the attempt KMS key is pending deletion on 2026-09-14. RC26
remains current, but the consumed objective transfers no result and must not be reused. See
`docs/evidence/phase8/2026-08-15/aws-native-rc26-redshift-connect-attempt.json`.

PR #330 merged that sanitized attempt as protected main `730de0b`; exact-main CI run `31920702822`
passed all five jobs. The replacement correctness gate is
`docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives-v2.json`: it preserves exact
RC26, one manual execution, one success-conditional replay, paused scheduling, exact cleanup, and
the existing cumulative USD 3 allocation while isolating a 300-second Redshift connection window
under the unchanged 600-second runtime deadline. No AWS mutation may precede this objective
commit's protected review and exact-main CI.

That replacement objective was consumed by one exact RC26 manual task. Redshift's connection log
proves that the task reached the private endpoint, authenticated as the exact Fargate task role,
and set `application_name=dander` within one second. No runtime-user query entered
`SYS_QUERY_HISTORY`; the Python driver then waited during connection startup until the 300-second
socket timeout. This disproves cold wake-up and VPC reachability as the cause, but the provider
record cannot distinguish the missing startup response from a driver handling defect. Replay did
not start. Exact saved-plan cleanup removed all 25 platform and 36 data-plane resources; both
states and direct active inventories are empty, and the attempt KMS key is pending deletion on
2026-09-14. RC26 is not qualified and no result transfers. A focused connection-startup defect PR,
replacement candidate, and fresh protected objective must precede another AWS run. See
`docs/evidence/phase8/2026-08-15/aws-native-rc26-redshift-driver-startup-attempt.json`.

PR #332 merged that sanitized startup record as protected main `1fa3452`; exact-main CI run
`31923526315` passed all five jobs. PR #333 then merged the focused Serverless correction as
protected main `141fab6`; exact-main run `31924339366` passed all five jobs. The correction requests
the driver's base text protocol only for Redshift Serverless and leaves provisioned Redshift on the
official default. PR #334 merged private `0.9.0rc27` as protected main `d7ac61f`; exact-main CI run
`31925228450` passed all five jobs. Its exact wheel built private source-free multi-platform index
`sha256:bcf62d2c…4e09c`. Both runnable manifests report RC27; GCP, Kubernetes, and externally
projected AWS deployment selection plus rootless read-only conformance passed on both architectures,
and both platform attestations contain SPDX SBOM and SLSA provenance predicates. This is private
candidate publication evidence, not a live-provider, cost, benchmark, public-release, or support
pass. RC26 AWS results do not transfer. PR #335 merged the sanitized publication record as
protected main `ea3e260`; exact-main CI run `31926577710` passed all five jobs. The fresh AWS
correctness gate is
`docs/evidence/phase8/2026-08-16/aws-native-rc27-profile-objectives.json`: it binds one manual
execution and one success-conditional replay to exact RC27, `us-east-1`, paused scheduling, exact
cleanup, the existing cumulative USD 3 allocation, and the reviewed 300-second Redshift connection
timeout. PR #336 merged that gate as protected main `c348122`; exact-main CI run `31927276568`
passed all five jobs.

The exact RC27 manual run and success-conditional replay then both succeeded with zero provider
retries and the expected digest. Redshift retained three distinct canonical rows after replay,
the replay affected zero source rows, all three assertions passed twice, Glue published the exact
manifest and lineage, PostgreSQL state supported both terminal runs, and the owned staging prefix
was empty. Reviewed saved-plan cleanup removed all 25 platform and 36 data-plane resources; both
Terraform states and direct active inventories are empty, while the exact private ECR digest is
retained. This closes the AWS-native correctness slice, not provider cost, scale, soak, public
release, or support: Cost Explorer was unavailable to the operator role and invoice data is still
pending. See `docs/evidence/phase8/2026-08-16/aws-native-rc27-profile.json`.

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
| `aws_native` | Fargate | Redshift | PostgreSQL | Glue | AWS Secrets Manager | exact RC27 manual/replay correctness and exact cleanup passed; provider cost, scale, soak, and support remain open |
| `kubernetes_portable` | Kubernetes | PostgreSQL | PostgreSQL | none | environment projection | local lifecycle accepted; Phase 8 live proof open |
| `azure_snowflake` | Azure Container Apps Jobs | Snowflake | PostgreSQL | none | Azure Key Vault | RC28 retry exposed a deterministic portable-identifier defect after preflight passed; replacement candidate required |
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
`31877158743`; fourteenth review then found two EC2 authorization blockers corrected in `b9735c9`.
Correction/current-main head `d8a18ec` passed run `31878215886`, and focused fifteenth review
accepted the correction. Docs-closure head `6ede9da` passed run `31879161660`, but sixteenth review
found missing create dimensions for existing VPC and route-table dependencies. Commit `e12ee59`
adds the qualification-tag-scoped grants; correction/docs head `0da600b` passed run `31879898267`,
and focused seventeenth review accepted the correction.
Private RC24 then applied and verified a drift-free 25-resource Fargate platform over its disposable
36-resource data plane with the exact candidate digest and a disabled schedule. One authorized
manual execution reached the runtime but exited before provider construction because the shared
Fargate identity hook incorrectly required Google federation for the AWS-native profile. The task
reported zero provider operations and wrote no rows; replay was not attempted. The exact platform
and data plane were removed, both Terraform states and direct owned-resource inventories are empty,
and the attempt's disabled KMS key is pending deletion on 2026-09-14. PR #314 corrected runtime
identity as protected main `7b47451`; exact-main run `31900109949` passed all five jobs. PR #315
corrected the separately discovered task-log read boundary as protected main `9c2faa6`; exact-main
run `31900852546` passed all five jobs. Its reviewed stage-zero plan applied `0/1/0`, the next plan
had no changes, and IAM simulation allowed the three qualified task-log reads while implicitly
denying an unrelated log group. This is preserved failed-attempt and cleanup evidence, not a
qualification pass; runtime source changed, so RC25 was built as the replacement candidate.
PR #319 then merged the exact RC25 AWS objective as protected main `c79b3d8`; exact-main run
`31904727106` passed all five jobs. The first RC25 platform apply was interrupted by a transient
CloudWatch Logs DNS failure. Its reviewed 13-create reconciliation reached the stable
`dander-controller-failures` rule, then failed before any execution because stage zero allowed
EventBridge tag/target reads only for the hyphen-suffixed deployment form. The 21-resource partial
platform and all 36 data-plane resources were removed, both Terraform states and direct owned
inventories are empty, and the disabled platform KMS key is pending deletion on 2026-09-14. RC25
remained the exact candidate; the live lane paused until the bounded exact-rule read reached
protected main and its reviewed stage-zero update was drift-free. Sanitized evidence is in
`docs/evidence/phase8/2026-08-15/aws-native-rc25-platform-attempt.json`. PR #320 merged the exact
stable-rule read as protected main `7155d54`; its stage-zero update applied `0/1/0`, the next plan
had no changes, and IAM simulation allowed both qualified reads for the stable and named rules
while denying both reads for an unrelated rule. A fresh source-free RC25 data plane and platform
then applied from reviewed saved plans and had no drift. The authorized manual task resolved its
AWS secret and obtained Redshift credentials, but the Serverless workgroup began cold-starting
network interfaces after that request and the configured 30-second connector timeout expired. The
task recorded zero provider operations and rows; replay was not attempted. Exact saved-plan cleanup
removed all 25 platform and 36 data-plane resources, both states and direct inventories are empty,
and the platform KMS key is pending deletion on 2026-09-14. RC25 remains valid because the defect is
in the qualification timeout, not candidate code. PR #322 merged the 120-second replacement
objective as protected main `ea625e3`; exact-main run `31911384116` passed all five jobs. The next
run projected that exact timeout from runbook commit `c14c6fa`, whose exact-main run `31912057557`
passed all five jobs. Its reviewed 36-resource data-plane and 25-resource platform plans applied
cleanly and had no drift. One authorized manual task connected to Redshift and created its temporary
table, then COPY failed because the runtime IAM database user lacked ASSUMEROLE permission on the
configured S3 staging role. The task role carried the expected `dander_runtime` database-role tag,
but the fixture's `GRANT ASSUMEROLE ON default TO ROLE dander_runtime FOR COPY` did not confer the
effective permission required by the explicit COPY role. Replay did not start. Saved-plan cleanup
removed all 25 platform and 36 data-plane resources; both states and direct owned-resource
inventories are empty, and the attempt KMS key is pending deletion on 2026-09-14. Provider invoice
data is still pending. This candidate defect requires its own focused implementation PR and a new
private candidate before the complete objective reruns. See
`docs/evidence/phase8/2026-08-15/aws-native-rc25-redshift-cold-start-attempt.json` and
`docs/evidence/phase8/2026-08-15/aws-native-rc25-copy-assumerole-attempt.json`.
Interactive Azure and OCI authentication was subsequently restored and
verified through provider APIs. Azure has zero Dander-named resources. OCI retains the accepted
Phase 7 foundation and private image history with zero active Container Instances; that retained
no-drift baseline must be preserved. Private RC27 satisfies the protected replacement-candidate
publication gate and its protected AWS objective. The exact manual/replay correctness slice passed,
and exact cleanup left both Terraform states and active owned inventories empty. AWS invoice data
is still pending, so the profile is not fully qualified and no support claim transfers. Every other
provider still requires its own committed exact objective manifest and separate lane. None inherits
a support claim. Sanitized details are in
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
local RC23 observed equal rows and both transports, but its recorded 1,400-byte recommendation
omitted field-name bytes counted by the writer. The corrected harness verifies that its ten-row
fixture is 1,490 logical bytes and completes bounded lookahead before opening a transaction.

Private RC24 reran the committed corrected objective against disposable TLS PostgreSQL 15.18 in
its exact source-free 2 CPU/512 MiB image. Across five repetitions at 1, 10, 100, 1,000, and 5,000
rows, COPY and DIRECT produced equal canonical rows and emitted their selected transports. COPY
medians were 8, 8, 10, 22, and 80 ms; DIRECT medians were 9, 8, 12, 42, and 185 ms. Because DIRECT
lost at the first sampled size, there is no contiguous DIRECT-winning prefix; the measured
recommendation is zero rows and zero bytes, so global defaults remain disabled. All seven approved
objectives passed in 2,650 ms with 177,127,424 bytes peak RSS, zero staging relations, exact
schema/container/network/volume cleanup, and USD 0 measured local cost. This closes corrected local
crossover only; protected review, hosted cost, and other applicable RC24 reruns remain open.

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

PR #338 merged the five exact RC27 objective files as protected main `6ff041f`; exact-main CI run
`31942160724` passed all five jobs before cluster creation. One named kind 1.32.2 arm64 cluster then
ran exact private RC27 against TLS PostgreSQL 15.18 with PostgreSQL state/warehouse, catalog `none`,
and an existing environment-projected Secret. Correctness, bulk, incremental, transform, and
PostgreSQL-specific failure all passed under the reviewed 2 CPU/512 MiB limit, 600-second deadline,
zero launcher retries, reporter-sidecar collection, and USD 0 local ceiling. Bulk processed 700,000
rows in 21.730 seconds; the incremental target finished with 301,500 rows; transform passed 21
assertions; and all four failure probes recovered as expected. All five reports bind exact RC27,
its immutable index digest, the approved objectives, and non-estimated USD 0 cost.

Two earlier cluster preflights failed closed before candidate creation: the TLS init container
dropped the ownership capability it needed, then kind retained candidate content without the
private registry reference. The corrected harness ran the TLS copy as PostgreSQL uid 70 and bound a
temporary local tag to the verified exact index with `imagePullPolicy: Never`; neither failure is a
candidate result. PostgreSQL retained zero Dander schemas and zero staging relations, the namespace
reported zero Warning events, and the cluster, node container, namespace, in-cluster Secrets/TLS
material, and temporary tag were deleted. See
`docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-scale-attempts.json`. This closes the
named final-candidate local profile and five-class launcher-scale slice, not hosted Kubernetes
scale/cost, remaining launcher classes, soak, public release, or support.

PR #339 merged that sanitized evidence as protected main `b73fafc`; exact-main CI run
`31943674409` passed all five jobs. The next dependency-ordered Kubernetes class is bounded memory.
`docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-bounded-memory-objectives.json` binds
exact RC27 and `kubernetes_portable` to the accepted passing RC22 retry workload: 2.6 million rows,
2.7248 GB logical input, 1,000-row batches, an externally enforced 256 MiB candidate limit, and the
unchanged 80% peak-RSS ceiling. The disposable kind 1.32.2 arm64 Job retains 2 CPU, TLS PostgreSQL
15.18 at 2 CPU/1 GiB, a 600-second deadline, zero retries, reporter-sidecar collection, and a USD 0
local ceiling. PR #340 merged that objective as protected main `72a422e`; exact-main CI run
`31944524241` passed all five jobs before execution. Exact RC27 then processed all 2.7248 GB in
129.180 seconds at 20,127 rows/second with 176,115,712 bytes peak RSS, below the 214,748,364.8-byte
ceiling. Both containers exited zero with no retry or restart, PostgreSQL retained no Dander schema
or staging relation, and the successful cluster reported zero Warning events and USD 0 local cost.
An initial harness-only preflight used the current-source Python path rather than RC27's packaged
`/usr/local/bin/python`; benchmark code did not start, the immutable image was inspected, and the
owned cluster was recreated before the passing run. The cluster, node container, in-cluster
Secrets/TLS material, and temporary candidate tag were deleted. No RC22 result transferred.

PR #341 merged the bounded-memory report and attempts ledger as protected main `f864a2b`;
exact-main CI run `31945860151` passed all five jobs. The next focused launcher class is concurrent
pipelines. `docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-concurrency-objectives.json`
binds exact RC27 and `kubernetes_portable` to the same protected 2.6-million-row/256 MiB benchmark
configuration while approving only four independent 5,000-row pipelines, stale-fence rejection,
throughput measurement, cleanup, and USD 0 local cost. The kind 1.32.2 arm64 runner retains 2 CPU,
TLS PostgreSQL 15.18 at 2 CPU/1 GiB, a 600-second deadline, zero retries, and reporter-sidecar
collection. The coupled bounded phase is rerun only because the accepted benchmark script precedes
its concurrency probe with that phase; it receives no additional qualification claim. Protected
merge and exact-main CI must precede execution, and neither the RC22 concurrency result nor the
incidental measurement from the bounded-memory run transfers.

PR #342 merged that objective as protected main `7dc51f8`; exact-main CI run `31946605370` passed
all five jobs before cluster creation. Exact RC27 then completed all four independent 5,000-row
pipelines in 334.55 ms at 59,781.789 rows/second and rejected the stale publication fence. The
candidate, TLS preflight, and reporter exited zero without retries or restarts; PostgreSQL retained
zero Dander schemas or staging relations; and the successful cluster reported zero Warning events
and USD 0 local cost. A first harness-only PostgreSQL storage preflight failed before the candidate
Job existed, so the non-root PGDATA initialization was corrected and the owned cluster was
recreated before execution. The cluster, node container, in-cluster Secrets/TLS material, and
temporary candidate tag were deleted. This closes the local concurrency class once its sanitized
report and attempts ledger pass protected review; it does not add a second bounded-memory claim or
close crossover, hosted scale/cost, soak, public release, or support.

PR #343 merged that sanitized concurrency evidence as protected main `bd7489d`; exact-main CI run
`31948875002` passed all five jobs, and its isolated operator TLS package was moved to Trash. The
next focused launcher class is crossover.
`docs/evidence/phase8/2026-08-16/kubernetes-rc27-postgresql-crossover-objectives.json` binds exact
RC27 and `kubernetes_portable` to the corrected RC24 workload: COPY and DIRECT across 1, 10, 100,
1,000, and 5,000 rows, 128-byte payloads, five repetitions, SCD1 equality, and the 1 MiB direct
ceiling. It approves canonical equality, both observed transports, measured crossover, the measured
threshold (including zero when no contiguous DIRECT-winning prefix exists), cleanup, and USD 0
local cost. The disposable kind 1.32.2 arm64 Job retains 2 CPU/512 MiB, TLS PostgreSQL 15.18 at
2 CPU/1 GiB, a 600-second deadline, zero retries, reporter-sidecar collection, and rootless
read-only candidate execution. Protected merge and exact-main CI must precede execution; RC24's
local result and zero threshold do not transfer.

PR #344 merged that objective as protected main `4166afb`; exact-main CI run `31949803615` passed
all five jobs before cluster creation. One disposable kind 1.32.2 arm64 Job then ran exact private
RC27 against TLS PostgreSQL 15.18 and passed all seven objectives. COPY and DIRECT produced equal
SCD1 rows at every size; DIRECT tied COPY at 1 and 10 rows, then lost at 100, 1,000, and 5,000 rows,
so this measured environment-specific recommendation is 10 rows / 1,490 logical bytes. That result
does not transfer RC24's zero threshold and does not tune a product default. The Job processed
61,110 rows in 2.433 seconds at 25,117.139 rows/second with 177,549,312 bytes peak RSS, zero retries
or restarts, zero Warning events, no database residue, and non-estimated USD 0 local cost. The
cluster, node container, in-cluster Secrets/TLS material, and temporary candidate tag were deleted.
PR #345 merged the sanitized report and one-attempt ledger as protected main `366ce8a`; exact-main
CI run `31951009601` passed all five jobs. Hosted Kubernetes scale/cost and soak remain open.

The next focused objective is one bounded-memory final audit on a disposable one-node zonal GKE
Standard cluster. `docs/evidence/phase8/2026-08-16/gke-standard-rc27-postgresql-bounded-memory-objectives.json`
binds exact RC27 to the protected 2.6-million-row/2.7248-GB workload, a 256 MiB candidate limit,
the unchanged 80% peak-RSS gate, 2 CPU, TLS PostgreSQL 15.18, a 600-second deadline, zero candidate
retries, reporter collection, and exact owned-resource cleanup. The run uses at most USD 0.50 of
the existing USD 0.75 `retained_gcp_soak_and_final_audits` allocation; provider billing must post
before the cost objective may pass. Protected merge and exact-main CI must precede GCP mutation.

PR #346 merged that objective as protected main `b01bf8b`; exact-main CI run `31952323045` passed
all five jobs. The later canonical-typecheck rail merged as main `1256213`; exact-main run
`31953203115` also passed all five jobs before execution, and the accepted benchmark script was
unchanged from exact RC27. One disposable zonal GKE Standard 1.35.6 cluster then ran the single
approved candidate attempt on an on-demand `e2-standard-4` amd64 node against rootless TLS
PostgreSQL 15.18. Exact RC27 processed all 2.7248 GB in 356.685 seconds at 7,289.345 rows/second
with 179,863,552 bytes peak RSS, below the 214,748,364.8-byte ceiling. Both containers exited zero
with no retry or restart, and PostgreSQL retained no Dander schema or staging relation.

An initial Job failed before candidate code started because the immutable RC27 image exposes
Python at `/usr/local/bin/python`, not the current-source Dockerfile path. Local image inspection
corrected the second and final infrastructure attempt without adding a candidate retry. Exact
cleanup removed the namespace, TLS and credentials, cluster, node, disk, GKE firewall rules,
run-created default network, custom node service account and IAM grants; Compute Engine and GKE
API activation returned to its disabled prestate. The raw report is preserved exactly and records
`catalog=postgresql` although no catalog operation ran; any later derived final report must correct
that metadata explicitly rather than rewriting the raw record. All non-cost objectives passed,
but provider billing has not posted, so the normalized result remains `not_evaluated`. See
`docs/evidence/phase8/2026-08-16/gke-standard-rc27-postgresql-bounded-memory-attempts.json`.

## Current exit recommendation

Phase 8 remains open. Exact private RC27 passed the protected artifact gate and the AWS-native
manual/replay correctness slice: three canonical rows survived duplicate-free replay, Glue and
PostgreSQL state paths completed, the schedule stayed disabled, and exact cleanup removed every
owned disposable resource. Provider invoice data has not posted and the operator role cannot read
Cost Explorer, so AWS cost remains `not_evaluated`; this is not a support promotion. PR #337 merged
the sanitized evidence as protected main `df018e6`; exact-main CI run `31941210969` passed all five
jobs. Each benchmark,
provider, optimization, or live-defect lane starts from fresh protected `main`; rerun only
materially affected evidence plus the eventual final-candidate closure matrix.
Remaining work includes provider-posted cost finalization for the completed GKE audit;
remaining benchmark classes/providers and Kubernetes soak;
hosted-provider and pairwise live proofs; scale/cost reports for every first-class warehouse and
launcher; remaining canonical-profile evidence; release-candidate soak; profile operator docs; and
the frozen support matrix.
