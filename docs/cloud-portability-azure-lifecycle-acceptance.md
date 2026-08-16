# Azure Container Apps lifecycle acceptance

Status: accepted on 2026-08-12; public-candidate supplement passed, disposable resources removed,
and retained GCP unchanged.

The canonical proof qualifies only the named Azure Container Apps, Snowflake OAuth warehouse,
PostgreSQL state, no-catalog, and Azure Key Vault composition. The selected pipeline must bind both
credential environment names to declared Key Vault secret identifiers. A separate portability
proof qualifies the Azure-to-BigQuery identity path for BigQuery, Dataplex, and GCP Secret Manager.
Neither proof qualifies any other cloud/warehouse pair or promotes Azure beyond experimental use.

## Approval boundary

Before provider registration or any write, record explicit per-provider ceilings for Azure,
Snowflake, and any retained-GCP verification plus approval to publish the source-free candidate.
Approval must cover the specific provider registrations, disposable subnet, saved-plan applies,
image publication and copy, secret writes, and job executions.

Stage zero creates the user-assigned managed identity whose Azure-assigned client ID is required by
the validated launcher profile. Therefore the exact source-free candidate is generated and
published after the reviewed stage-zero apply, using that real non-secret client ID, but before the
platform plan or apply and before any job execution. Record its accepted digest at that point.
Approved repeat attempts remain bounded by the same per-attempt ceilings.

Credentials, secret values, warehouse rows, Terraform state, and binary plans stay outside the
repository. Saved plans and state use the secured operator artifact directories described in
`infra/azure/README.md`.

## Ordered proof

1. Confirm the signed-in subscription, reviewed region, current operator IP, approved ceilings,
   publication approval, and provider-registration approval.
2. Register only the required providers, then review and apply the exact stage-zero plan. Read the
   Azure-assigned managed-identity client ID from the stage-zero output.
3. Generate the source-free project with that client ID, publish one candidate to staging GAR,
   record its accepted digest, and copy the same OCI index into ACR without rebuilding.
4. Create the approved disposable Container Apps subnet and require its `Microsoft.KeyVault`
   service endpoint. Review and apply the exact `--foundation-only` platform plan, which creates
   the environment, vault, and scoped roles but no job or alert. Populate only the
   manifest-declared PostgreSQL and Snowflake credentials outside Terraform. Then review and apply
   the normal platform plan, which may create jobs only after their Key Vault references exist.
5. During Snowflake setup, grant the runtime role `CREATE SCHEMA` on only the named disposable
   database. Do not grant database ownership, `ALL PRIVILEGES`, or account-level authority. Project
   a current OAuth token for that exact role through the manifest's configured environment name,
   then run the read-only gate:

   ```bash
   dander azure canonical-preflight \
     --deployment azure_snowflake \
     --pipeline warehouse_fixture \
     --expected-image danderphase6.azurecr.io/dander/runtime@sha256:DIGEST
   ```

   In addition to the Azure deployment and declared-secret metadata checks, this reads the active
   Snowflake role's grants and requires `CREATE SCHEMA` on the exact configured database. It creates
   no schema and emits no credential, DSN, SQL row, or raw provider error. A failure here is setup
   evidence and stops before any Container Apps candidate execution or allowance.

6. Require initial execution, exact replay, overlapping-start fencing, interruption/cancellation,
   paused and UTC-scheduled behavior, retry exhaustion, bounded logs, and alert routing to match
   the platform contract.
7. Create a new version of one declared Key Vault secret outside Terraform. Because the job uses a
   versionless URI, allow Azure's documented refresh window, then start exactly one approved new
   execution and require the rotated credential to work. Do not automatically rerun on failure.
8. Exercise immutable-image rollback and restoration without rebuilding either image.
9. Remove disposable jobs, vault, logging, network, registry, identity, state, and warehouse proof
   objects in dependency order. Verify provider-owned cleanup and retained-GCP no drift.

## Sanitized evidence

Retain only commit and digest identities, normalized states and counts, pass/fail booleans,
timestamps, approved ceilings, cleanup results, and no-drift outcome. The read-only preflight may
retain declared secret names and enabled booleans; it never retains values or unrelated vault
entries. The machine-readable record is
[`docs/evidence/azure/2026-08-11/phase6.json`](evidence/azure/2026-08-11/phase6.json).

## Accepted result

- The source-free Azure/Snowflake/PostgreSQL/Key-Vault candidate used one byte-identical GAR/ACR
  OCI index, digest
  `sha256:a64d89a3beff1b56ed8b3b13f17b67f8f99d87e08ebf48e6ff01381ecdc94d59`.
  Its build-context revision was
  `0901a384d3bf1141b44bbf64268cff4f102cfc4fc2cbc8aaa28e94e9ded70fd`.
  It passed the read-only canonical preflight, manual and UTC-scheduled execution, replay,
  overlapping-start fencing, interruption, retry exhaustion, alert routing, versionless Key Vault
  rotation, and immutable rollback/restoration.
- The separate Azure-to-Google candidate at protected-main commit
  `eb074c58a9b3d8c1296c28849639a04c07fdb4bf` retained identical GAR/ACR content at digest
  `sha256:aa7da96e9b628c4bda5288a1a32edc1e2873c782459ce52d840248a60f851b4c`.
  One process queried BigQuery before and after credential refresh; a second bounded execution read
  one declared GCP secret and the corresponding Dataplex system entry without emitting values,
  query text, or rows. Disabling the exact workload-identity provider then failed closed.
- Public `dander-platform==0.9.0rc1`, tagged from protected-main commit `2b90f7ad`, installed outside
  the checkout and produced source-free digest
  `sha256:1e1bd9ed803b523626c6f4720caba92a84bf0f95473d0002e12371b1a4975519`.
  The same OCI content ran in ACR and GAR with provenance and SBOM attached. From Azure it repeated
  BigQuery access across credential refresh and GCP secret/Dataplex read-back. In an isolated GCP
  project it completed Greenhouse, HubSpot, exact replay, owned-data cleanup, and Terraform
  no-drift. This public-artifact smoke supplements rather than repeats the earlier complete
  canonical Snowflake lifecycle.
- Disposable Snowflake objects, Entra/GCP federation resources and principals, Azure jobs and
  infrastructure, isolated-GCP proof data, and proof images were removed. Azure's mandatory purge
  protection retains only the deleted Key Vault tombstone until 2026-11-10; it is not an active
  vault. Fresh isolated-GCP and retained-GCP plans reported exact `No changes.` Retained GCP had 28
  stage-zero and 113 platform resources as no-ops; no retained-GCP apply occurred.

The Phase 6 exit gate passes because one accepted release digest passed both Azure launcher
conformance and the complete named Azure profile, and the subsequently published candidate passed
the required public-artifact supplement. Azure remains experimental pending Phase 8 scale,
throughput, cost, soak, pairwise-profile, and release qualification. Phase 7 did not begin.
