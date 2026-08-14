# Phase 8 qualification baseline

This is the authoritative starting inventory for Phase 8. It records accepted foundations and
open release gates without claiming scale, cost, soak, pairwise, or support qualification.
Protected `main` at `536b31b701a67a5b7eeb68e09e1d87a4c59898f9` passed complete CI after the
Phase 7 evidence merge.

## Accepted foundations

| Boundary | Current evidence | Phase 8 boundary |
|---|---|---|
| GCP native | Supported retained Cloud Run/BigQuery profile, current schedules, lifecycle history, and current-equivalent no drift | Exact qualification-candidate scale, cost, and soak evidence remains open |
| Fargate to GCP | Public `0.8.0rc8` passed manual/scheduled lifecycle, replay, interruption, alerts, rollback, cleanup, and no drift | Scale qualification remains open; this is not the AWS-native profile |
| Kubernetes portable | PostgreSQL native-profile conformance and the existing-cluster Helm lifecycle contract pass locally | Exact-candidate live-cluster execution, alerting, scale, and soak remain open |
| Azure canonical | The Snowflake/PostgreSQL/Key-Vault lifecycle passed; the separate BigQuery/GCP identity profile passed refresh and revocation | Exact-candidate scale, cost, pairwise, and soak remain open |
| OCI canonical | Public `0.9.0rc17` passed the complete PostgreSQL/OCI-Vault lifecycle on one digest | Exact-candidate scale, cost, pairwise, and soak remain open |
| Warehouses | BigQuery, PostgreSQL, Snowflake, and Redshift produced equal normalized common-scalar rows; provider-specific bounded qualifications also exist | Existing PostgreSQL, Snowflake, and Redshift reports are not Phase 8 scale reports; BigQuery has no normalized scale report |
| Audits | Protected CI passes tests, lint, typing, dependency audit, distribution install, Terraform validation/security, secret scan, and image scans | Repeat against the final candidate after Phase 8 implementation |

## Open gates and dependency order

1. Publish the normalized report contract and keep historical partial reports `not_evaluated`.
2. Add sanitized failure diagnostics. Retained ServiceNow runs on 2026-08-10 and 2026-08-11
   ended as `unexpected_error`, and neither the run ledger nor Cloud Logging retained enough safe
   exception identity to diagnose them.
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
aggregate Phase 8 ceiling of USD 10 on 2026-08-14; USD 0 has been incurred. Each paid run must
still record its objective manifest and per-run allocation before mutation, preserve the dependency
order, and use the immutable candidate.

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
| `gcp_native` | Cloud Run | BigQuery | BigQuery | Dataplex | GCP Secret Manager | lifecycle accepted; Phase 8 open |
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

## Current exit recommendation

Phase 8 remains open. The exact unmet gates are protected review of the AWS-native implementation,
retained evidence for the safe diagnostic patch, one immutable qualification candidate,
exact-candidate Kubernetes and pairwise live proofs, approved scale/cost reports for every
first-class warehouse and launcher, current canonical-profile evidence, release-candidate soak,
final audits, and the frozen support matrix.
