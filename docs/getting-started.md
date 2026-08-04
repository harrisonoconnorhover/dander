# Hosted Greenhouse quickstart

This guide starts from the published package and ends with one manually verified Greenhouse Job
Board pipeline in BigQuery. It does not require a Dander source checkout or a Greenhouse account.
Cloud provisioning has no promised duration and may incur charges.

## Prerequisites

- An existing, dedicated, disposable GCP project with billing already linked.
- Project Owner on that disposable project (or equivalent permissions to enable services, create
  the state bucket and service accounts, and grant the roles listed by the generated stage-zero
  Terraform).
- Python 3.12, `uv`, Terraform 1.9 or newer, Docker with Buildx, and Google Cloud CLI.
- An operator email address for hosted failure notifications.

Set the two private operator values in your shell; do not commit them:

```bash
export DANDER_PROJECT="your-disposable-project-id"
export DANDER_ALERT_EMAIL="operator@example.com"

gcloud auth login
gcloud config set project "$DANDER_PROJECT"
gcloud auth application-default login
gcloud auth application-default set-quota-project "$DANDER_PROJECT"
```

## Install and scaffold

```bash
uv tool install dander-platform==0.2.0
dander --version
dander new my-dander-project
cd my-dander-project
dander validate
dander run greenhouse_jobs --dry-run --project "$DANDER_PROJECT"
```

The generated manifest keeps the schedule paused. The dry-run performs no credential or network
access and should identify one Greenhouse endpoint and one selected model.

## Provision the paused platform

Read the confirmation carefully, then allow Dander to create stage zero, build and push the pinned
runtime image, and apply the reviewed platform plan:

```bash
dander init \
  --project "$DANDER_PROJECT" \
  --failure-alert-email "$DANDER_ALERT_EMAIL" \
  --apply
```

The command stores Terraform state in the hardened bucket
`$DANDER_PROJECT-dander-state`, keeps local operator artifacts outside the project directory, and
leaves the Greenhouse scheduler paused. The generated project disables Dander's managed cost guard,
so this path does not require a billing-account ID or billing-account IAM permissions. Dander is not
managing, limiting, or preventing cloud spending in this configuration; disabling the guard does not
prevent or cap cloud charges.

The managed cost guard is optional. To use its simulation-first budget, Pub/Sub notification path,
billing-shutoff function, and guarded runtime preflight, set
`platform.safety.require_guarded_free_tier` to `true` and pass `--billing-account`. That opt-in path
requires permission to grant the bootstrap identity Billing Account Administrator on the linked
billing account. Review those additional billing-account and project-level IAM changes before any
apply. The guard remains simulation-first unless `--live-cost-guard` is explicitly supplied.

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

Change only `pipelines.greenhouse_jobs.paused` to `false` in `dander.yaml`, rerun `dander validate`,
and repeat the same `dander init ... --apply` command. Review the confirmation and resulting plan;
the expected platform change is the scheduler state plus the immutable image generated from the
updated project content.

Use [upgrading.md](upgrading.md) for later releases and safe reruns. If installation or the first
hosted run cannot be completed from this page, report it as a defect in the current supported
release rather than consulting Dander's source.
