---
id: DANDER-111
title: Prove keyless Azure-to-Google workload federation
status: in_progress
phase: 6
---

# DANDER-111 — Prove keyless Azure-to-Google workload federation

## Goal

Run the accepted OCI digest from Azure Container Apps against the existing BigQuery, Dataplex, and
GCP Secret Manager boundaries without any Azure client secret or Google service-account key, then
observe Google credential refresh in the same process.

## Acceptance

- [x] The Azure launcher accepts one typed Entra application ID URI and Google WIF audience only
  for the named BigQuery/Dataplex/GCP-secrets profile.
- [x] Runtime Google credentials renew from the attached user-assigned managed identity without a
  credential file or globally exported token.
- [x] Terraform creates a single-tenant Entra audience and restricts the Google provider and
  impersonation grant to the selected Azure identity object ID.
- [x] GCP Secret Manager references remain runtime inputs and are not converted into Azure Key
  Vault secrets.
- [x] The same-process refresh probe is bounded, emits no query text, tokens, credentials, or rows,
  and has no automatic paid rerun.
- [x] Focused Python and provider-mocked Terraform contracts pass locally.
- [ ] An accepted public candidate passes live BigQuery access before and after Google credential
  refresh plus GCP secret access and Dataplex read-back under approved ceilings.
- [ ] Revocation fails closed; all disposable Azure/Entra/GCP proof resources are removed; retained
  GCP finishes with no Terraform drift; sanitized evidence is merged.

## Boundary

No Azure provider registration, Terraform apply, image publication/copy, job execution, Google IAM
mutation, or paid query is authorized by this ticket. Local construction evidence does not promote
Azure or the cross-cloud profile to supported.
