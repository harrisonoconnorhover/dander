# Azure Container Apps lifecycle acceptance

Status: protocol prepared; no live Azure resource or paid proof has run.

This protocol qualifies only the named Azure Container Apps, Snowflake OAuth warehouse,
PostgreSQL state, no-catalog, and Azure Key Vault composition. The selected pipeline must bind both
credential environment names to declared Key Vault secret identifiers. It does not qualify
Azure-to-BigQuery or any other cloud/warehouse pair.

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
5. Run the read-only gate:

   ```bash
   dander azure canonical-preflight \
     --deployment azure_snowflake \
     --pipeline warehouse_fixture \
     --expected-image danderphase6.azurecr.io/dander/runtime@sha256:DIGEST
   ```

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
entries. Live evidence remains pending until the full approved sequence passes.
