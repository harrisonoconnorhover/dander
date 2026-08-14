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
| Kubernetes portable | Exact private RC22 passed the local existing-cluster lifecycle, including alert visibility and cleanup | Hosted-provider proof, normalized scale/cost, and soak remain open |
| Azure canonical | The Snowflake/PostgreSQL/Key-Vault lifecycle passed; the separate BigQuery/GCP identity profile passed refresh and revocation | Exact-candidate scale, cost, pairwise, and soak remain open |
| OCI canonical | Public `0.9.0rc17` passed the complete PostgreSQL/OCI-Vault lifecycle on one digest | Exact-candidate scale, cost, pairwise, and soak remain open |
| Warehouses | BigQuery, PostgreSQL, Snowflake, and Redshift produced equal normalized common-scalar rows; exact RC22 also passed local PostgreSQL bounded-memory, concurrency, bulk-throughput, and incremental objectives | PostgreSQL correctness, transform, failure, crossover, and hosted cost plus all exact-candidate BigQuery, Snowflake, and Redshift scale reports remain open |
| Audits | Protected CI passes tests, lint, typing, dependency audit, distribution install, Terraform validation/security, secret scan, and image scans | Repeat against the final candidate after Phase 8 implementation |

## Open gates and dependency order

1. Publish the normalized report contract and keep historical partial reports `not_evaluated`.
2. Completed on 2026-08-14: sanitized failure diagnostics merged and exact RC21 retained execution
   `dander-servicenow-incidents-7kxl4` emitted bounded class chains and a numeric status without
   messages, bodies, credentials, DSNs, or rows. This proves safe causal identity, not the external
   ServiceNow root cause.
3. Complete protected review of the locally implemented AWS-native
   Fargate/Redshift/PostgreSQL/Glue/AWS-Secrets profile. The factory and saved-plan Terraform path
   now accept only that exact composition or the previously accepted GCP composition; no live AWS
   qualification or support claim exists yet.
4. Cut one immutable release candidate after the AWS-native implementation and diagnostics merge.
5. Use that exact candidate for the Kubernetes live profile, provider scale matrix, pairwise
   matrix, canonical live-profile reruns, and release-candidate soak.
6. Repeat release audits and freeze the compatibility and limitation documents only after every
   required report passes.

The operator approved cloud mutations, conservative provider-specific SLO selection, and an
aggregate Phase 8 ceiling of USD 10 on 2026-08-14. Private RC22 publication and the retained GCP
diagnostic ran within pre-recorded USD 0.75 and USD 1.25 allocations; provider-measured charges
have not posted, so no exact cost is claimed. Each paid run must still record its objective manifest
and per-run allocation before mutation, preserve the dependency order, and use the immutable
candidate.

Private `0.9.0rc22` at protected main `aebecade458e85c5d3b077c1f2a96ccd6ee825aa` is the exact
qualification candidate. Its source-free multi-platform index is
`sha256:ce395dda3865691d2300f57577fb9b5297031293f77c89f6adc34f60853947c3`; both deployment
selectors passed read-only runtime inspection. Public RC20 remains unchanged.

## Pre-candidate release readiness

Commit `2d020d15fc52` passed a local release-readiness audit on 2026-08-14: wheel and source archive
inspection, clean installation and generated-project validation for both artifacts, the full
multi-provider runtime import, dependency audit, all Terraform format/validation and mocked module
tests, Helm lint/render, the main container runtime contract, and the OCI controller container
contract. Trivy configuration/image scans, the Git-history secret scan, protected CI, and the
independent completion review were not available locally. This preflight does not substitute for
repeating every audit on the final source-free candidate.

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
| `aws_native` | Fargate | Redshift | PostgreSQL | Glue | AWS Secrets Manager | local implementation complete; protected review and Phase 8 qualification open |
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
No mutation or spend occurred. These cases remain open rather than inheriting a support claim;
sanitized details are in `docs/evidence/phase8/2026-08-14/provider-credential-blockers.json`.

## Current PostgreSQL scale evidence

Exact RC22 ran inside its immutable source-free image against disposable TLS PostgreSQL 15.18.
The approved bulk workload passed 500,000 narrow rows at 38,681.727 rows/second and 200,000 wide
rows at 9,032.608 rows/second. The approved incremental workload applied a 3,000-row delta to a
300,000-row seed at 16,483.516 rows/second, finished with the exact 301,500-row target, and rejected
cursor regression. Both schemas and all temporary staging relations were removed; local measured
service cost was USD 0.

RC22's PostgreSQL writer factory exposes only `PostgreSQLCopyWriter`; it has no bounded direct
transport to compare with COPY. The PostgreSQL crossover class therefore remains open as an exact
candidate capability gap rather than receiving synthetic passing evidence.

## Current exit recommendation

Phase 8 remains open. The safe diagnostic retained-provider gate, corrected immutable candidate,
exact-candidate local Kubernetes lifecycle, local PostgreSQL bounded-memory/concurrency reports,
local PostgreSQL bulk/incremental reports, and exact-candidate GCP profile rerun are complete. The
exact unmet gates are the other benchmark classes and providers, Kubernetes scale/soak,
hosted-provider and pairwise live proofs, approved scale/cost reports for every first-class
warehouse and launcher, remaining canonical-profile evidence, release-candidate soak, final
audits, and the frozen support matrix.
