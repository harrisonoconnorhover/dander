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

## Runtime image promotion

After stage zero exists and a source-free candidate has an accepted local artifact record, copy the
same OCI index into ACR. This is a live registry mutation and requires separate publication, cost,
and execution approval before running it:

```bash
dander image-promote-azure \
  --source-image us-central1-docker.pkg.dev/example/dander/runtime@sha256:DIGEST \
  --subscription-id 11111111-1111-4111-8111-111111111111 \
  --acr-name danderphase6 \
  --acr-repository dander/runtime
```

The command authenticates Docker through the signed-in Azure identity. It never requests ACR
administrator credentials and never rebuilds. The deterministic destination tag is accepted only
when stable ACR metadata reports the source index digest and the immutable digest reference exposes
the same platform manifests. A successful copy writes the ignored local record
`.dander/runtime-artifact-azure.json`.

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
The plan grants the exact signed-in Terraform operator `Key Vault Secrets Officer` so that person
can create and rotate the declared proof secrets; the job identity remains limited to `Key Vault
Secrets User`. Secret values remain an operator action outside Terraform and must not appear in a
plan, state file, shell history, or committed evidence.

## Job lifecycle

The lifecycle CLI resolves the exact job from `dander.yaml` and the named deployment. Status,
verification, and bounded logs are read-only; run, cancel, and replay ask for confirmation because
they are live provider operations:

```bash
dander azure run --deployment azure_snowflake --pipeline warehouse_fixture
dander azure status --deployment azure_snowflake --pipeline warehouse_fixture
dander azure logs --deployment azure_snowflake --pipeline warehouse_fixture \
  --execution-name dander-00626d3b5f01-abc1234 --limit 100
dander azure cancel --deployment azure_snowflake --pipeline warehouse_fixture \
  --execution-name dander-00626d3b5f01-abc1234
dander azure replay --deployment azure_snowflake --pipeline warehouse_fixture \
  --execution-name dander-00626d3b5f01-abc1234
```

Azure assigns execution names. Replay is allowed only from a terminal execution and starts the
same immutable job template; Dander's persisted inclusive cursor remains responsible for
idempotent logical replay. Log reads query only the selected execution and enforce an explicit
row limit.

## Boundaries

- ACR administrator credentials and Storage shared keys remain disabled; state network access
  defaults to deny and admits only the reviewed operator `/32`.
- The runtime identity receives only `AcrPull` and `Key Vault Secrets User` in this root.
- The signed-in plan operator receives `Key Vault Secrets Officer` only on this deployment vault;
  no group, subscription-wide secret administrator, or second operator is inferred.
- Key Vault network access defaults to deny and admits only Azure's trusted-service path plus the
  reviewed operator IP.
- Secret values are an operator action outside Terraform and must never enter a plan or state.
- The locally tested image-copy and job-operation commands are not live evidence. Actual image
  copy, job lifecycle execution, external identity federation, live profile proof, and support
  promotion remain separate Phase 6 gates.
