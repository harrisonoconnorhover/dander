# Hosted Greenhouse quickstart

This guide starts from the published package and ends with one manually verified Greenhouse Job
Board pipeline in BigQuery. It does not require a Dander source checkout or a Greenhouse account.
Cloud provisioning has no promised duration and may incur charges.

## Prerequisites

- An existing, dedicated, disposable GCP project with billing already linked.
- Either a cloud administrator for the one-time stage-zero handoff, or an installer with these
  predefined project roles: Service Usage Admin, Storage Admin, Artifact Registry Administrator,
  Service Account Admin, and Project IAM Admin. Project Owner is not required.
- Python 3.12, `uv`, Terraform 1.9 or newer, Docker with Buildx, and Google Cloud CLI.
- An operator email address for hosted failure notifications.

Set the two private operator values in your shell; do not commit them:

```bash
export DANDER_PROJECT="your-disposable-project-id"
export DANDER_ALERT_EMAIL="operator@example.com"
export DANDER_STATE_BUCKET="$DANDER_PROJECT-dander-state"
export DANDER_ADMIN_MEMBER="user:$(gcloud config get-value account)"
export DANDER_OPERATOR_DIR="$HOME/.dander/$DANDER_PROJECT/bootstrap"

gcloud auth login
gcloud config set project "$DANDER_PROJECT"
gcloud auth application-default login
gcloud auth application-default set-quota-project "$DANDER_PROJECT"
```

## Install and scaffold

```bash
uv tool install dander-platform==0.9.0rc1
dander --version
dander new my-dander-project
cd my-dander-project
dander validate
dander run greenhouse_jobs --dry-run --project "$DANDER_PROJECT"
```

The generated `dander.platforms.yaml` keeps the schedule paused. The dry-run performs no credential or network
access and should identify one Greenhouse endpoint and one selected model.

## Create the Terraform backend once

Terraform cannot create the bucket that stores its own state. An administrator must create this one
hardened bucket before the first plan; every later infrastructure change is plan-reviewed:

```bash
gcloud storage buckets create "gs://$DANDER_STATE_BUCKET" \
  --project "$DANDER_PROJECT" \
  --location US \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update "gs://$DANDER_STATE_BUCKET" \
  --versioning \
  --update-labels=managed-by=dander,purpose=terraform-state
```

## Plan and apply stage zero

`init-admin-plan` first performs a read-only permission check and reports exact missing permissions.
It then adopts the backend bucket into stage-zero state and saves a plan outside the project:

```bash
dander init-admin-plan \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_STATE_BUCKET" \
  --admin-member "$DANDER_ADMIN_MEMBER" \
  --operator-artifact-dir "$DANDER_OPERATOR_DIR"

terraform -chdir=infra/bootstrap-admin show -no-color \
  "$DANDER_OPERATOR_DIR/dander-admin-bootstrap.tfplan"

dander init-admin-apply \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_STATE_BUCKET" \
  --admin-member "$DANDER_ADMIN_MEMBER" \
  --operator-artifact-dir "$DANDER_OPERATOR_DIR"
```

The apply command uses the exact saved plan. Stage zero creates the bootstrap service account,
Artifact Registry repository, and its project-level provisioning roles. The approved operator gets
Token Creator only on that bootstrap account; later image publication and Terraform operations use
impersonation rather than Project Owner.

## Publish, plan, and apply the paused platform

```bash
dander image-publish \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_STATE_BUCKET" \
  --failure-alert-email "$DANDER_ALERT_EMAIL"
```

The command confirms before pushing, prints the immutable image digest, and prints the complete
`init-platform-plan` command. Run that printed command, review its saved plan, then apply it:

```bash
terraform -chdir=infra show -no-color dander-bootstrap.tfplan

dander init-platform-apply \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_STATE_BUCKET" \
  --bootstrap-service-account \
    "dander-bootstrap@$DANDER_PROJECT.iam.gserviceaccount.com"
```

The generated project leaves the Greenhouse scheduler paused and disables Dander's managed cost
guard. This path needs no billing-account ID or billing-account IAM. Dander is not managing,
limiting, or preventing cloud spending; disabling the guard does not prevent or cap charges.

The managed cost guard is optional. To use its simulation-first budget, Pub/Sub notification path,
billing-shutoff function, and guarded runtime preflight, set
`deployments.gcp_cloud_run.safety.require_guarded_free_tier` to `true` in
`dander.platforms.yaml` and pass `--billing-account`. That opt-in path
requires permission to grant the bootstrap identity Billing Account Administrator on the linked
billing account. Review those additional billing-account and project-level IAM changes before any
apply. Selecting GitHub WIF similarly requires Workload Identity Pool Admin during stage zero.
Neither permission is requested by the standard unguarded, non-WIF path. The guard remains
simulation-first unless `--live-cost-guard` is explicitly supplied.

## Run and verify Greenhouse

```bash
gcloud run jobs execute dander-greenhouse-public \
  --project "$DANDER_PROJECT" \
  --region us-central1 \
  --wait

dander metadata runs \
  --project "$DANDER_PROJECT" \
  --pipeline greenhouse_jobs \
  --limit 5

bq query --project_id="$DANDER_PROJECT" --use_legacy_sql=false \
  'SELECT COUNT(*) AS raw_rows FROM `raw.greenhouse_job_board_jobs`'
bq query --project_id="$DANDER_PROJECT" --use_legacy_sql=false \
  'SELECT COUNT(*) AS staging_rows FROM `staging.stg_greenhouse__jobs`'
```

The latest Dander run must be `succeeded` at `complete`, with one model and its declared tests. A
non-empty raw and staging relation proves the live public extraction and transform path.

## Enable the schedule only after proof

Change only `deployments.gcp_cloud_run.pipelines.greenhouse_jobs.paused` to `false` in
`dander.platforms.yaml`, rerun `dander validate`,
then repeat `image-publish`, `init-platform-plan`, plan review, and `init-platform-apply`. The
expected platform changes are the scheduler state and immutable image. Repeat `init-platform-plan`
afterward and require Terraform to report `No changes.`

Use [upgrading.md](upgrading.md) for later releases and safe reruns, and [rollback.md](rollback.md)
for restoring a known-good image. If installation or the first
hosted run cannot be completed from this page, report it as a defect in the current supported
release rather than consulting Dander's source.
