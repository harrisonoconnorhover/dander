# Phase 6 Azure-to-Google federation proof

This disposable root prepares the keyless Azure Container Apps to Google workload-identity
boundary. It creates one single-tenant Entra application audience, an OIDC workload identity pool
provider restricted to one existing Azure user-assigned identity object ID, one disposable Google
service account, and only the BigQuery, Dataplex, and named Secret Manager grants needed by the
bounded proof.

The Entra application is an audience, not a client credential. Dander requests its token through
Container Apps managed identity and exchanges it in memory; no client secret, service-account key,
external-account file, or token is written to Terraform inputs or runtime logs.

## Approval boundary

Do not run `terraform apply`, register Azure resource providers, publish an image, start a job, or
use paid Google services until the candidate and explicit per-provider ceilings have been approved.
Planning uses read-only provider calls but can still reveal account metadata, so keep saved plans,
state, variables, and logs in a secured operator directory outside the repository.

## Reviewed sequence

1. Copy this root to the secured operator directory and fill values from
   `terraform.tfvars.example`; use a new `proof_name` if an earlier Google pool is soft-deleted.
2. Run `terraform init`, save `terraform plan -out=dander-phase6-azure-google.tfplan`, and review
   `terraform show -no-color` before seeking apply approval.
3. Apply only that exact saved plan. Put the non-secret outputs
   `google_workload_identity_audience` and `azure_application_id_uri` into the typed Azure launcher
   configuration; the service-account output must match each proof pipeline's
   `runtime_service_account_id`. Pass the reviewed data-plane project to `init-azure-plan` through
   `--gcp-project`; version 2 keeps that operator/deployment scope out of the reusable platform
   profile.
4. Run the same accepted OCI digest through the Azure BigQuery profile. After separate live-cost
   approval, start exactly one refresh probe (there is no automatic rerun):

   ```console
   dander azure identity-refresh-probe \
     --deployment azure_bigquery \
     --pipeline warehouse_fixture \
     --project example-proof-project \
     --dataset raw \
     --table proof_rows
   ```

   Require a BigQuery query before and after the 600-second Google credential refresh, Secret
   Manager access, Dataplex read-back, and no credential/token text in logs or committed evidence.
5. Destroy this root from a separately reviewed destroy plan. Verify the Azure proof resources are
   gone, run the retained GCP no-drift plan, and retain only sanitized evidence.

Local Terraform mock tests validate the identity pinning and generated runtime coordinates. They
are not live federation evidence and do not promote the profile to supported.
