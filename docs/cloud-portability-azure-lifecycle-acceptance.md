# Azure Container Apps lifecycle acceptance

Status: protocol prepared; no live Azure resource or paid proof has run.

This protocol qualifies only the named Azure Container Apps, Snowflake OAuth warehouse,
PostgreSQL state, no-catalog, and Azure Key Vault composition. The selected pipeline must bind both
credential environment names to declared Key Vault secret identifiers. It does not qualify
Azure-to-BigQuery or any other cloud/warehouse pair.

## Approval boundary

Before provider registration or any write, record an accepted source-free candidate digest and
explicit per-provider ceilings for Azure, Snowflake, and any retained-GCP verification. Approval
must cover the specific provider registrations, disposable subnet, saved-plan applies, image copy,
secret writes, and job executions. There are no automatic paid reruns.

Credentials, secret values, warehouse rows, Terraform state, and binary plans stay outside the
repository. Saved plans and state use the secured operator artifact directories described in
`infra/azure/README.md`.

## Ordered proof

1. Confirm the signed-in subscription, reviewed region, current operator IP, candidate digest, and
   approved ceilings. Confirm the existing delegated Container Apps subnet exposes the
   `Microsoft.KeyVault` service endpoint.
2. Review and apply the exact stage-zero and platform plans. Populate only the manifest-declared
   PostgreSQL and Snowflake credentials outside Terraform.
3. Run the read-only gate:

   ```bash
   dander azure canonical-preflight \
     --deployment azure_snowflake \
     --pipeline warehouse_fixture \
     --expected-image danderphase6.azurecr.io/dander/runtime@sha256:DIGEST
   ```

4. Require initial execution, exact replay, overlapping-start fencing, interruption/cancellation,
   paused and UTC-scheduled behavior, retry exhaustion, bounded logs, and alert routing to match
   the platform contract.
5. Create a new version of one declared Key Vault secret outside Terraform. Because the job uses a
   versionless URI, allow Azure's documented refresh window, then start exactly one approved new
   execution and require the rotated credential to work. Do not automatically rerun on failure.
6. Exercise immutable-image rollback and restoration without rebuilding either image.
7. Remove disposable jobs, vault, logging, network, registry, identity, state, and warehouse proof
   objects in dependency order. Verify provider-owned cleanup and retained-GCP no drift.

## Sanitized evidence

Retain only commit and digest identities, normalized states and counts, pass/fail booleans,
timestamps, approved ceilings, cleanup results, and no-drift outcome. The read-only preflight may
retain declared secret names and enabled booleans; it never retains values or unrelated vault
entries. Live evidence remains pending until the full approved sequence passes.
