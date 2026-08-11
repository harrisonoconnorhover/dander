# Azure Container Apps Jobs Terraform

This root turns validated `io.dander.execution/v1` Azure projections into Container Apps Jobs. It
uses Azure Storage remote state, one existing ACR and user-assigned managed identity from the
stage-zero root, a Log Analytics-backed Container Apps environment, an RBAC-enabled Key Vault, and
optional failed-execution alerts to an existing Action Group. Terraform creates secret references,
never secret values.

Planning and applying are deliberately separate. Both use the signed-in Azure CLI identity and
Entra authentication for Storage; neither Terraform provider automatically registers Azure
resource providers.

## Stage zero

Choose globally unique lowercase Storage and ACR names. Keep the operator artifact directory
outside the checkout because it contains the saved plan and temporary local state:

```bash
dander init-azure-admin-plan \
  --subscription-id 11111111-1111-4111-8111-111111111111 \
  --location eastus \
  --resource-group dander-phase6 \
  --state-storage-account danderphase6state \
  --state-allowed-ip 203.0.113.10 \
  --acr-name danderphase6 \
  --managed-identity-name dander-phase6-runtime \
  --operator-artifact-dir "$HOME/Library/Application Support/Dander/terraform/azure-admin"
```

Replace the documentation-only IP with the operator's current public IPv4 address. Azure treats
this exact-IP firewall entry with `/32` semantics.
After separate approval for provider registration and resource costs, review the saved plan and
run the matching `init-azure-admin-apply` command. The apply migrates its initial local state into
the firewall-restricted, private, versioned Azure Storage container. It grants the authenticated
operator only the Blob data role needed to use that Entra-authenticated backend. Record
`runtime_identity_client_id` from the printed Terraform output command and place that non-secret
identifier in the named launcher configuration.

## Platform plan

After an accepted OCI release is copied into ACR by digest, plan the selected deployment:

```bash
dander init-azure-plan \
  --deployment azure_snowflake \
  --state-resource-group dander-phase6 \
  --state-storage-account danderphase6state \
  --key-vault-allowed-ip 203.0.113.10 \
  --container-image danderphase6.azurecr.io/dander/runtime@sha256:DIGEST
```

This creates a saved plan only. An optional existing delegated subnet can be selected with
`--infrastructure-subnet-id`; selecting it makes the environment internal. An existing reviewed
Action Group can be selected with `--alert-action-group-id`. Paused pipelines remain manual jobs;
active schedules use Azure's UTC-only five-field cron.

Replace the documentation-only Key Vault IP with the operator's current public IPv4 address. Key
Vault defaults to deny, admits that exact operator IP for secret administration, and retains the
Azure trusted-service path used by the managed-identity secret reference.

Run `dander init-azure-apply` only after reviewing the exact saved plan and receiving explicit
approval for the live Azure changes. Then use `dander azure verify` for read-only checks of the
subscription, environment, logs, job trigger/resources/image, managed identity, ACR, and Key Vault.

## Boundaries

- ACR administrator credentials and Storage shared keys remain disabled; state network access
  defaults to deny and admits only the reviewed operator `/32`.
- The runtime identity receives only `AcrPull` and `Key Vault Secrets User` in this root.
- Key Vault network access defaults to deny and admits only Azure's trusted-service path plus the
  reviewed operator IP.
- Secret values are an operator action outside Terraform and must never enter a plan or state.
- Image copy, job lifecycle operations, external identity federation, live profile proof, and
  support promotion are separate Phase 6 gates.
