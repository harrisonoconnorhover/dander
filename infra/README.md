# infra/ — Terraform for the dander bootstrap CLI

`dander init` runs these modules to stand up the GCP data platform. GCP-first; a cloud-specific
detail stays inside each module so `aws/`/`azure/` siblings can be added later without changing the
call sites (mirrors the `SecretStoreProvider` / `ComputeProvider` abstractions in code).

## Modules

| Module | Provisions |
|---|---|
| `modules/bigquery` | `raw` / `staging` / `marts` plus durable `dander_meta` control/catalog dataset. **Implemented.** |
| `modules/scheduled-job` | Existing stage-zero Artifact Registry repository plus independent least-privilege Cloud Run/Scheduler resources for every `dander.yaml` pipeline. **Implemented.** |
| `modules/secret-manager` | Named secret containers and per-secret runtime access; never secret values. **Implemented.** |
| `modules/github-wif` | Repository/ref-scoped GitHub OIDC and a keyless deployment identity. **Implemented.** |
| `modules/cost-guard` | Project budget, Pub/Sub, and simulation-first Gen 2 billing kill switch. **Implemented.** |
| `kubernetes/chart/dander` | Versioned Helm chart for an existing conforming cluster. **Locally validated; not live-qualified.** |
| `azure/bootstrap-admin` | Azure Storage state, ACR, and user-assigned runtime identity. **Live-proven for the named experimental Phase 6 profiles.** |
| `azure/modules/container-apps-jobs` | Container Apps Jobs, Key Vault references, Log Analytics, alerts, and optional network placement. **Live-proven for the named experimental Phase 6 profiles.** |
| `local` | Digest-only same-origin HTTPS Druff + Control Compose profile with a durable local GraphStore. **Implemented; live proof awaits current immutable images.** |
| `oci/bootstrap-admin` | Native OCI Object Storage state and a private digest-addressed OCIR repository. **Implemented and locally validated; live proof pending.** |
| `oci` | Private VCN/subnet, default Vault and rotating key, Container Instance resource-principal policy, Logging, and Notifications foundation. **Implemented and locally validated; live proof pending.** |

The main root always calls `modules/bigquery` and can opt into the remaining workload modules. The
one-time `infra/bootstrap-admin` root creates the remote-state bucket, the Artifact Registry
repository, the separate `dander-bootstrap` service account, its provisioning roles, and the
approved caller's impersonation binding. The main root never creates those preconditions and
requires an impersonated service account. The normal batteries-included path performs both stages,
builds/pushes the runtime image, and applies the manifest:

```bash
uv run dander init \
  --project my-gcp-project \
  --billing-account ABCDEF-123456-ABCDEF \
  --apply
```

The state bucket defaults to `<project>-dander-state`. Because Terraform cannot store the state
that creates its own backend, the CLI creates only that hardened/versioned bucket imperatively and
immediately imports it into permanent stage-zero Terraform state. Secret values remain an explicit
post-bootstrap operator action.

Run stage zero first:

```bash
uv run dander init-admin-plan \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --admin-member user:operator@example.invalid \
  --operator-artifact-dir "$HOME/Library/Application Support/Dander/terraform/bootstrap-admin/my-gcp-project"
uv run dander init-admin-apply \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --admin-member user:operator@example.invalid \
  --operator-artifact-dir "$HOME/Library/Application Support/Dander/terraform/bootstrap-admin/my-gcp-project"
```

Then plan the platform only through the emitted bootstrap identity:

```bash
uv run dander init-platform-plan \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --bootstrap-service-account dander-bootstrap@my-gcp-project.iam.gserviceaccount.com
uv run dander init-platform-apply \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --bootstrap-service-account dander-bootstrap@my-gcp-project.iam.gserviceaccount.com
```

The granular commands remain available when separate plan approvals are required.

To plan the complete hosted runtime, first push an image and resolve its immutable digest:

```bash
uv run dander init \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --bootstrap-service-account dander-bootstrap@my-gcp-project.iam.gserviceaccount.com \
  --enable-runtime \
  --billing-account ABCDEF-123456-ABCDEF \
  --container-image us-central1-docker.pkg.dev/my-gcp-project/dander/dander@sha256:DIGEST \
  --config dander.yaml \
  --github-repository owner/repository \
  --enable-cost-guard
```

This still plans by default. Add `--apply` only after reviewing the saved plan. Secret values must
be added separately with `gcloud secrets versions add`; Terraform intentionally never receives
them. GitHub WIF grants Artifact Registry access only on the Dander repository, Cloud Run developer
access, and `actAs` only on Dander's runtime identities.

The cost guard is simulation-only by default. `--live-cost-guard` allows its over-budget handler
to unlink billing and therefore appears by name in the apply confirmation. That action can stop
services and delete resources, while delayed billing reports can still exceed the configured
amount. Deploying the Gen 2 function uses billable Cloud Build, Cloud Run, Storage, and Artifact
Registry components; a plan never asserts that the result will cost exactly zero.

For hosted pipelines, keep `sandbox.auto.tfvars.example` only as a non-secret Terraform input-shape
reference. Use `dander init-platform-plan` so Dander compiles the manifest into validated execution
projections and supplies them to Terraform; do not copy the example into a direct plan. Keep every
new pipeline paused for its first apply. Run each Cloud Run Job manually, verify its guarded write,
selected transform tests, and registry compilation, then enable only the proven schedule in a
reviewed plan. Each runtime identity can create BigQuery jobs, edit the shared Dander datasets,
inspect Pub/Sub guard wiring, read billing budget metadata, and access only its declared secrets.
Each scheduler identity can invoke only its pipeline's named Cloud Run Job.

## Rules (see `steering/01-security.md` and `steering/languages/terraform.md`)

- **Remote GCS backend** for state — never local state committed to the repo.
- Azure uses an Entra-authenticated Azure Storage backend; its temporary bootstrap state and saved
  plans remain outside the repository.
- OCI uses Terraform's native Object Storage backend with a short-lived `SecurityToken` profile;
  its temporary bootstrap state and saved plans also remain outside the repository.
- **No secret values** in `.tf`/`.tfvars`; reference Secret Manager. Commit only `*.tfvars.example`.
- Project id / region are always parameterized, never hard-coded.
- Stage-zero applies run through the CLI using the approved administrator. Platform applies use
  the exact saved plan while impersonating only `dander-bootstrap`.

## Deployment verification

The protected, manual end-to-end workflow and its required WIF environment variables are documented
in [`docs/live-proof.md`](../docs/live-proof.md). It is the only supported path for the owned
HubSpot proof and live Storage Write/Dataplex mutations.

After `dander init --apply` and Terraform backend initialization, run the read-only verifier:

```bash
uv run dander verify deployment \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --state-prefix dander/state \
  --runtime-job dander-greenhouse-public \
  --scheduler-job dander-greenhouse-public-daily \
  --secret-id hubspot-private-app-token \
  --json evidence/bootstrap-summary.json
```

The command checks project state, BigQuery datasets, remote GCS state, optional Cloud Run and
Scheduler resources, project- and billing-account-scoped runtime IAM, dataset bindings, and named
Secret Manager containers. A failed check is retained in the JSON summary and exits non-zero; the
artifact contains only statuses, stable names, counts, and timestamps. The runtime image can be
pinned with `--runtime-image`.
