# Dander Platform Release Audit

Audited on 2026-08-15 against the product promise in `steering/00-project-overview.md`.
“Live-proven” means the behavior was observed in a disposable provider account or retained GCP
project. “Implemented” means automated tests cover the contract while an optional provider or
cloud path remains outside the live proof.

## Requirement evidence

| Product requirement | Status | Current evidence |
|---|---|---|
| One CLI and typed project manifest | Live-proven | `dander.yaml` declares four daily connector pipelines and one executable graph; `dander validate`, provisioning, execution, and metadata inspection use the same CLI. |
| Batteries-included GCP infrastructure | Live-proven | Reviewed Terraform owns remote state, Artifact Registry, BigQuery datasets, per-pipeline IAM, Cloud Run jobs, Scheduler jobs, Secret Manager containers, failure alerts, and the optional cost guard. |
| Additive multi-pipeline hosting | Live-proven | Greenhouse, HubSpot, Salesforce, and ServiceNow coexist with distinct jobs, schedules, and runtime identities. The graph job is separately deployed and paused. |
| Independently installed connectors | Live-proven | Public Salesforce `0.3.1` and ServiceNow `0.2.2` packages are exact-pinned, discovered through Dander plugin API v1, installed into a source-free image, and exercised against disposable provider accounts. |
| Shared enterprise authentication | Live-proven for retained plugins | Dander core supplies Salesforce OAuth2 JWT and ServiceNow OAuth2 client credentials while Terraform grants each runtime access only to its declared Secret Manager resources. |
| Bounded ingestion and idempotent BigQuery writes | Live-proven for hosted SCD1 | Greenhouse, HubSpot, Salesforce, and ServiceNow have completed hosted ingestion and replay checks without duplicate destination keys; Salesforce applies its cursor server-side. |
| Owned transforms and tests | Live-proven | Retained pipelines execute Dander's `ref()` DAG and declared BigQuery assertions after ingestion. |
| Durable state and optional read capabilities | Live-proven | Run history, leases, watermarks, count, connection-check, and targeted-read paths were exercised for the relevant retained plugins without provider write-back. |
| Single metadata spine | Live-proven | The run ledger and catalog store pipeline lifecycle, source/model schema, lineage, tests, and governed metrics in BigQuery; Dataplex publication remains optional. |
| Canonical visual authoring | Live-proven for the bounded graph slice | Druff opens and saves canonical `PipelineGraph`, discovers presentation-safe connector and operation descriptors, starts an already-deployed run, and previews a non-applyable full-manifest plan through Dander's loopback service. |
| Local hosted Control plane | Live-proven for the D7 local profile | Exact Dander/Druff digests passed loopback HTTPS, synthetic OIDC/PKCE, graph restart persistence, byte-equal rendering, stable second-up identities, rollback/restoration, and exact cleanup. This is not real-provider or cloud qualification. |
| Azure launcher portability | Live-proven for the named experimental profiles | One immutable source-free digest passed the Azure/Snowflake/PostgreSQL/Key-Vault lifecycle; public `0.9.0rc1` passed Azure-to-Google refresh, secret, catalog, revocation and isolated-GCP smoke. |
| OCI launcher portability | Live-proven for the named experimental profile | Public `0.9.0rc17` passed the OCI Container Instances/PostgreSQL/PostgreSQL/no-catalog/OCI-Vault lifecycle; unsupported OCI-to-Google identity fails closed. |
| Infrastructure reconciliation safety | Live-proven | Exact private RC22 changed only five retained Cloud Run job images; the following current-equivalent 113-resource platform plan reported exact `No changes.` |
| Phase 8 support qualification | Open | Protected private RC22 retains Kubernetes/GCP and seven-class PostgreSQL evidence; private RC24 passed corrected local PostgreSQL crossover. Private multi-platform RC27 is the replacement candidate after three focused AWS-native corrections and inherits no historical result. |

## Current release and deployment record

- Public Dander beta: `0.9.0rc20`; public Salesforce connector: `0.3.1`; public ServiceNow connector:
  `0.2.2`.
- Public Dander `0.9.0rc20` was published from protected-main commit
  `75c5654e95439eaf18e90fbacc849799f4fe42b6` and immutable tag `v0.9.0rc20` by trusted-publishing
  run `31815063258`. Its public wheel and source-distribution hashes matched the workflow
  artifacts, and a fresh no-cache PyPI-only install passed CLI version, scaffold, project, and
  Terraform validation outside every checkout. RC20 packages the D6 service/startup contract and
  D7 local Compose assets; it published no current Dander or Druff container image. The later
  local-only proof loaded exact reviewed images and did not promote provider support.

- The D7 local hosted Control proof used exact active and rollback Dander/Druff digests and passed
  a synthetic OIDC/PKCE browser journey, API and browser graph restart persistence, equal Compose
  rendering, stable second-up identities, rollback/restoration, and verified cleanup. Accepted
  local image objects remain available for later D7 profiles; the disposable registry and TLS
  material were removed. See `docs/evidence/local/2026-08-14/d7-control-plane.json`.
- Public Dander `0.9.0rc19` was published from protected-main commit
  `cad383b8ac74e8ba0ce0b3b92c66b0a5a93a306b` and immutable tag `v0.9.0rc19` by trusted-publishing
  run `31785512985`. Its wheel and source distribution contain the complete deterministic
  `io.dander.control.contracts/v1` bundle at
  `sha256:695791dfda6058d68453d9e146146d5cdda1439d86c40a7ec249cb4e14a12be3`; a fresh PyPI-only
  installation matched all 37 manifest file hashes and passed CLI, scaffold, and Terraform
  validation. RC19 packages every graph-store adapter, but only GCS is live-qualified; S3, Azure,
  and OCI remain unpromoted. See `docs/control-contracts.md` for artifact hashes and links.
- Isolated portability acceptance used Dander `0.7.0rc2` and the public plugin candidates in a
  source-free image. The same accepted OCI content passed local and Cloud Run conformance.
  Salesforce ingested all four endpoints, published five governed models and Dataplex metadata,
  replayed without duplicate keys or cursor regression, skipped an overlapping run, recovered
  from a bounded interruption, released its lease, removed staging, and finished with Terraform
  no-drift. See `docs/cloud-portability-phase1-acceptance.md`.
- Public Dander `0.8.0rc1` passed the separate Phase 1B feasibility gate: one source-free
  multi-platform index remained byte-identical across GAR and ECR, completed Cloud Run
  conformance on AMD64, and queried BigQuery from one ARM64 Fargate task before and after keyless
  Google credential refresh. All proof resources were removed, and Fargate remains experimental.
  See `docs/cloud-portability-phase1b-acceptance.md`.
- Public Dander `0.8.0rc8` passed the complete lifecycle gate for the named
  Fargate-to-BigQuery/GCP composition: manual and scheduled execution, replay, interruption,
  alert routing, image rollback, cleanup, and four final no-drift plans. Fargate remains
  experimental pending scale/profile qualification. See
  `docs/cloud-portability-fargate-lifecycle-acceptance.md`.
- Protected-main Dander `0.8.0rc8` passed the bounded Phase 5 common-scalar fixture on BigQuery,
  PostgreSQL, Snowflake, and Redshift with one equal normalized hash, exact replay, and verified
  cleanup. This is warehouse correctness evidence, not provider or profile support promotion.
- The source-free Azure candidate at digest
  `sha256:a64d89a3beff1b56ed8b3b13f17b67f8f99d87e08ebf48e6ff01381ecdc94d59`
  passed the named Azure/Snowflake/PostgreSQL/Key-Vault lifecycle, including scheduling, replay,
  fencing, interruption, retry exhaustion, alerts, rotation, rollback, and cleanup. Public
  `0.9.0rc1` then passed the separate Azure-to-Google refresh, GCP-secret, Dataplex, revocation,
  isolated-GCP smoke, cleanup, and no-drift proof. Azure remains experimental pending Phase 8; see
  `docs/cloud-portability-azure-lifecycle-acceptance.md`.
- Public Dander `0.9.0rc17` passed the complete Phase 7 lifecycle for the named OCI profile using
  equal GAR/OCIR index digest
  `sha256:190e9caa082efcd72e9a2a586c082c266e48f99a0bb69b99e30114e3c8c886b9`.
  The proof covered scheduling, replay, fencing, interruption, retry exhaustion, Vault
  application-secret rotation, rollback/restoration, alarm-to-topic routing, cleanup, OCI no
  drift, and retained-GCP no drift. OCI remains experimental pending Phase 8; see
  `docs/cloud-portability-oci-lifecycle-acceptance.md`.
- The five retained jobs use private Phase 8 candidate Dander `0.9.0rc22` index
  `sha256:ce395dda3865691d2300f57577fb9b5297031293f77c89f6adc34f60853947c3`.
  Authenticated Salesforce manual/replay and Scheduler-created Greenhouse runs passed on its
  Linux/AMD64 manifest; provider-measured cost remains pending.
- Exact RC22 passed protected CI run `31825533602` and the local final-candidate repeat: clean
  artifact installs, full runtime imports, Terraform/Helm, dependency and Git-history secret
  audits, rootless read-only runtime checks, and HIGH/CRITICAL Trivy infrastructure/main-image/
  OCI-controller scans. See `docs/evidence/phase8/2026-08-14/rc22-local-audit.json`.
- Private arm64 RC23 at `sha256:8bd35188…3064` adds the bounded PostgreSQL direct path and passed
  local artifact/runtime, dependency, source-secret, infrastructure, and image preflight. Its TLS
  PostgreSQL run observed equal DIRECT/COPY rows, but completion review found the 1,400-byte
  recommendation omitted writer-counted field-name bytes; that threshold objective is invalid.
  The corrected 1,490-byte calculation must be rerun on a protected multi-platform successor.
- Private RC24 at `sha256:b7eadc7e…9488` is that protected-main source-free successor. Its exact
  wheel and source distribution passed package inspection; the GAR index has runnable amd64/arm64
  manifests, SBOM, and provenance. Both architectures reported RC24, the image contains the flat
  AWS qualification assets without repository source, and GCP/Kubernetes/external-AWS selectors
  plus read-only conformance passed without provider access. PR #299 merged the sanitized evidence
  as `a66ce65`, and exact-main run `31884123337` passed all five jobs. No historical report
  transfers.
- Exact RC24 passed the corrected local PostgreSQL crossover objective. COPY and DIRECT produced
  equal canonical rows, both transports were observed, and cleanup was exact. DIRECT lost at the
  first sampled size, so the measured recommendation stays disabled at zero rows/bytes. All seven
  objectives passed with USD 0 local cost; hosted cost and applicable replacement-candidate reruns
  stay open.
- Private RC25 at `sha256:5a0d5520…2238` is the protected-main replacement after the AWS-native
  identity correction. Exact-main run `31902553474` passed all five jobs; its exact wheel built a
  source-free amd64/arm64 GAR index with SPDX SBOM and SLSA provenance. Both architectures reported
  RC25, GCP/Kubernetes/external-AWS selectors and rootless read-only conformance passed, and no RC24
  result transfers. This is private publication evidence, not a live-profile, cost, or support pass.
- Private RC26 at `sha256:e63aef4b…d28e` is the protected-main replacement after the exact
  Redshift staging-role grant correction. Exact-main run `31915564765` passed all five jobs; its
  exact wheel built a source-free amd64/arm64 GAR index with SPDX SBOM and SLSA provenance. Both
  architectures reported RC26, GCP/Kubernetes/external-AWS selectors and rootless read-only
  conformance passed, and no RC25 result transfers. This is private publication evidence, not a
  live-profile, cost, or support pass.
- Private RC27 at `sha256:bcf62d2c…4e09c` is the protected-main replacement after the scoped
  Redshift Serverless startup correction. Exact-main run `31925228450` passed all five jobs; its
  exact wheel built a source-free amd64/arm64 GAR index with SPDX SBOM and SLSA provenance. Both
  architectures reported RC27, GCP/Kubernetes/external-AWS selectors and rootless read-only
  conformance passed, and no RC26 result transfers. This is private publication evidence, not a
  live-profile, cost, or support pass.
- Exact RC22 was copied byte-identically to private ECR for AWS-native preflight. Its immutable
  image lacked the selected AWS deployment, so no Fargate plan, task, or pipeline ran. The exact
  28-resource disposable data-plane destroy completed with empty qualification state/inventories
  and no AWS D7 change. Provider cost remains pending; this is cleanup evidence, not a profile pass.
- AWS qualification-baseline head `3ea34e2` passed all five protected jobs in run `31876449299`.
  Focused thirteenth review accepted the scoped forced-version cleanup correction and current-main
  integration. Reconciliation head `0c65e42` passed run `31877158743`; fourteenth review found two
  EC2 authorization blockers corrected in `b9735c9`. Correction/current-main head `d8a18ec` passed
  run `31878215886`, and focused fifteenth review accepted the correction. Docs-closure head
  `6ede9da` passed run `31879161660`; sixteenth review found missing VPC/route-table dependency
  dimensions corrected in `e12ee59`. Correction/docs head `0da600b` passed run `31879898267`, and
  focused seventeenth review accepted the correction. No cloud mutation occurred.
- Retained Druff image: `sha256:a5e255d6…871c`; public static URL:
  <https://dander-druff-yos2b3gbca-uc.a.run.app>.
- The four scheduled connector executions on 2026-08-05 completed successfully. Those executions
  preceded the final stable-image reconciliation; observation of subsequent scheduled runs remains
  in the active operator soak. A read-only review on 2026-08-14 found all four schedules enabled;
  that day's public Greenhouse run succeeded, while the other three daily schedules had not yet
  occurred. Their latest runs through 2026-08-13 were successful, but the 2026-08-10 and 2026-08-11
  ServiceNow failures were not diagnosable from the sanitized ledger or Cloud Logging. The soak
  gate therefore remains open.
- Greenhouse graph execution `dander-greenhouse-graph-7gn9z` completed successfully on 2026-08-04;
  its 13:00 schedule remains intentionally paused.

## Release boundaries

- Dander provisions inside an existing billing-linked GCP project; it does not create a project or
  attach billing. Its managed cost guard is simulation-first and is not a spending cap.
- The `0.6.0` Salesforce example supports read-only Accounts, Contacts, Opportunities, and
  Users; the retained soak still exercises its existing Accounts pipeline. ServiceNow supports
  one read-only incidents slice. Provider write-back and broad object/table coverage are not
  claimed.
- NetSuite is simulator-validated only; Workday lacks real-tenant acceptance. Live Storage Write
  and optional Dataplex publication are not implied by the retained proof.
- Druff is a public static browser shell. Its privileged operations remain in an operator-started,
  exact-origin Dander loopback service; Druff cannot apply Terraform or write the project manifest.

## Verdict

Dander demonstrates its intended beta vertical slice: one manifest and CLI reconcile an owned
GCP platform, independently installed connectors ingest into BigQuery, Dander transforms and tests
the data, one metadata spine records what happened, and Druff authors and operates a bounded graph
without becoming a second runtime. The D7 local hosted Control profile is live-qualified without
promoting a real identity provider or cloud profile. Phase 8 support qualification is not complete;
RC22's protected audit and RC23's local preflight are preserved. Qualification-baseline corrections
through `e12ee59` merged in PR #291 and passed exact-main CI at `3d7783c`. PR #298 merged private
RC24 at protected main `c19de39`; its corrected local PostgreSQL crossover passed with the DIRECT
recommendation disabled, but its AWS-native launch exposed the corrected identity defect. PR #317
merged private RC25 at protected main `f5935a6`; its AWS run exposed the Redshift staging-role
grant defect. PR #326 merged corrected private RC26 at protected main `f0fe54f`; exact-main run
`31915564765` passed and source-free multi-platform index `sha256:e63aef4b…d28e` is privately
published and inspected. Its AWS rerun exposed the Redshift Serverless startup response defect.
PR #334 merged corrected private RC27 at protected main `d7ac61f`; exact-main run `31925228450`
passed and source-free multi-platform index `sha256:bcf62d2c…4e09c` is privately published and
inspected. No historical
result transfers. The combined final-candidate audit and
remaining scale, cost, profile, soak,
operator-documentation, and support-freeze gates are recorded in
`docs/cloud-portability-phase8-qualification.md`.
