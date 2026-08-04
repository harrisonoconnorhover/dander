# Dander

[![CI](https://github.com/harrisonoconnorhover/dander/actions/workflows/ci.yml/badge.svg)](https://github.com/harrisonoconnorhover/dander/actions/workflows/ci.yml)

> [!WARNING]
> **Alpha software.** Dander `0.1.x` is being stabilized through a retained proof project and an
> independent-operator trial. Use a disposable GCP project, review every Terraform plan, and read
> the [known limitations](https://github.com/harrisonoconnorhover/dander/blob/main/docs/known-limitations.md)
> before relying on it. Only the latest patch in the current `0.x` minor is supported.

**An opinionated, self-hosted, GCP-native data platform you own** — ingest + transform + catalog
behind one CLI. A focused replacement for Informatica and a customizable stand-in for dbt.

> Think *"Terraform for your data platform."* `dander init` stands up the GCP infrastructure;
> `dander run` extracts your SaaS systems into BigQuery; the transform engine models the data; and
> a single metadata spine keeps your catalog and semantic layer in sync.

## Why it exists

Every existing tool does one slice: **dlt** ingests, **dbt** transforms, **Airbyte/Meltano** are
platforms but heavy or bring-your-own-everything. None ship an opinionated, self-hosted, GCP-native
system that fuses ingest + transform + catalog and that a small team fully owns — no per-row bill,
no vendor-consolidation risk. That's the gap dander fills.

### The wedge — what makes it different

1. **Batteries-included + self-provisioning.** One CLI provisions Secret Manager, IAM/WIF,
   Cloud Run, and BigQuery, then runs your pipelines.
2. **Enterprise SaaS auth as a first-class citizen.** Workday RaaS, NetSuite OAuth1 TBA, Xactly —
   the connectors that are painful everywhere else, as vetted, typed auth *strategies*.
3. **A single metadata spine.** One YAML per model/source projects to SQL **and** your data catalog
   (Dataplex aspects) **and** a semantic/agent registry. Define once, project everywhere.
4. **You own all of it.** Open source, customizable, GCP-opinionated.

## Architecture

Hybrid ingestion (dlt for standard REST APIs, hand-rolled extractors for gnarly enterprise sources —
both behind one `Source` interface) → explicit, idempotent BigQuery **write patterns** → our own
**transform engine** (`ref()` DAG → topological execution + tests) → **catalog** publication.
See `steering/00-project-overview.md` for the full module map and decision log.

Repository checks and branch-protection guidance live in [`docs/ci.md`](docs/ci.md).
The approval-gated, WIF-authenticated live proof is documented in
[`docs/live-proof.md`](docs/live-proof.md); keep the pull request draft until its sanitized
workflow artifact is reviewed.

Operator-facing documentation:

- [Hosted Greenhouse quickstart](https://github.com/harrisonoconnorhover/dander/blob/main/docs/getting-started.md)
- [Upgrades and safe reruns](https://github.com/harrisonoconnorhover/dander/blob/main/docs/upgrading.md)
- [Known limitations](https://github.com/harrisonoconnorhover/dander/blob/main/docs/known-limitations.md)
- [Post-release operator soak](https://github.com/harrisonoconnorhover/dander/blob/main/docs/operator-soak.md)
- [Security and supported versions](https://github.com/harrisonoconnorhover/dander/blob/main/SECURITY.md)
- [Release notes](https://github.com/harrisonoconnorhover/dander/blob/main/CHANGELOG.md)

## Stack

Python 3.12 (app + CLI) · BigQuery SQL (transforms) · Terraform/HCL (infra) · YAML (config).

## Install a release candidate

The Python distribution is named `dander-platform` because the `dander` name on PyPI belongs to a
different project. The import package and command remain `dander`:

```bash
uv tool install dander-platform==0.1.0
dander --version
dander new my-data-platform
cd my-data-platform
dander validate
```

`dander new` creates a complete, paused starter project: a public Greenhouse connector, one model,
the Docker runtime context, and Dander's Terraform modules. It refuses to overwrite an existing
path. You do not need to clone this repository to use the released CLI. Follow the
[hosted quickstart](https://github.com/harrisonoconnorhover/dander/blob/main/docs/getting-started.md)
to provision and manually verify the paused Greenhouse pipeline before enabling its schedule.

## Repo map

```
src/dander/     core · security · ingestion · writer · executor · transform · catalog · state · cli
infra/          Terraform modules (secret-manager, iam, compute-run, bigquery)
connectors/     per-source YAML configs
models/         SQL transform models + YAML sidecars
tests/
steering/       binding rules for humans + agents (read these)
tickets/        work items
scripts/        dev tooling (e.g. the workflow monitor)
.claude/        agent workforce, feature workflow, /feature command
```

## Developer setup (macOS)

**Prerequisites**

- [Homebrew](https://brew.sh)
- **[uv](https://docs.astral.sh/uv/)** — manages the Python toolchain and dependencies (it will
  fetch Python 3.12 itself, so you don't need to install Python separately)
- **git**
- **[Claude Code](https://claude.com/claude-code)** — only if you want to run the agentic `/feature`
  workflow (see below). Not needed to build or test the Python package.

**Install**

```bash
brew install uv                 # one-time: install uv
git clone <repo-url> dander && cd dander
uv sync --extra dev             # install app + dev deps into .venv (fetches Python 3.12 if needed)
```

That's it — `uv sync` creates the virtualenv, installs everything from `pyproject.toml`, and pins
it in `uv.lock`.

## Everyday commands

All commands run through `uv run` (no need to activate the venv manually):

```bash
uv run ruff check .        # lint
uv run ruff format .       # auto-format
uv run mypy                # strict type-check
uv run pytest              # run the test suite
uv run dander --help       # the CLI (init / run)
uv run dander validate     # validate dander.yaml and every pipeline reference
uv run dander metadata list --project my-gcp-project
```

**Green baseline** = `ruff check`, `ruff format --check`, `mypy`, and `pytest` all pass. Keep it
green; the `pr-review` agent enforces it on every ticket.

## Runnable Greenhouse paths

The free first path reads published jobs from Greenhouse's public Job Board API. It uses
Greenhouse's own board as a live example, needs no Greenhouse account or credential, and exercises
the same dlt → BigQuery writer path as private connectors:

```bash
uv run dander run greenhouse_job_board --dry-run --project my-gcp-project
uv run dander run greenhouse_job_board --guarded-free-tier --project my-gcp-project
```

To read another organization's published jobs, copy the connector and replace `greenhouse` in
`/greenhouse/jobs` with the public board token from its job-board URL. Public GET requests return
published job data only; they do not expose candidates, applications, or other private records.

### Additional real public job boards

`lever_job_board` reads Spotify's published Lever postings and exercises the provider's
`skip`/`limit` pagination. `ashby_job_board` reads Ashby's published jobs, including publicly
displayed compensation where present. Both are credential-free, read-only examples using official
public APIs:

```bash
uv run dander run lever_job_board --dry-run --project my-gcp-project
uv run dander run ashby_job_board --dry-run --project my-gcp-project
```

See the official [Lever Postings API](https://github.com/lever/postings-api) and
[Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
documentation. Public boards can change or migrate without notice; copy the connector and replace
the documented site/board token to target another organization's published jobs.

### Deterministic fault injection

The local synthetic vendor remains the repeatable test for behavior that must not be provoked
against public services: duplicate/update scenarios and deterministic 429/500 recovery.

```bash
uv run dander-synthetic-api
uv run pytest tests/ingestion/test_synthetic_vendor.py
```

Do not use public profiles as substitute candidate data. Candidate- or contact-shaped integration
tests should contain invented people in an account you control. HubSpot offers
[free developer test accounts](https://developers.hubspot.com/docs/getting-started/account-types)
with sample CRM data; connecting one requires the account owner to create and authorize the test
app, so no credential or account is embedded in this repository.

The canonical `greenhouse` connector reads private candidates and jobs through Harvest v3. It
uses OAuth 2.0 client credentials, caches expiring access tokens, and applies the token to every
paginated request. Export the two credential references locally, or point each environment value
at a full Secret Manager version resource in cloud execution:

```bash
read -r SECRET_GREENHOUSE_CLIENT_ID
read -rs SECRET_GREENHOUSE_CLIENT_SECRET && printf '\n'
export SECRET_GREENHOUSE_CLIENT_ID SECRET_GREENHOUSE_CLIENT_SECRET
uv run dander run greenhouse --project my-gcp-project
```

Create Harvest v3 credentials in Greenhouse under **Configure → Dev Center → API Credential
Management**, choose **Harvest V3 (OAuth)**, and grant only the read scopes for candidates and
jobs. By default Greenhouse attributes requests to the integration service user associated with
the credential. An optional integer `auth_options.subject` can select a different Greenhouse user.
See Greenhouse's [v3 authentication guide](https://harvestdocs.greenhouse.io/docs/authentication).

`greenhouse_harvest_v1_legacy` preserves API-key compatibility during migration only. Greenhouse
states that Harvest v1/v2 become unavailable after 2026-08-31; new deployments should not use it.

`connectors/marketo.example.yaml` is the second standard-REST template. Copy it to
`connectors/marketo.yaml`, replace `MUNCHKIN_ID`, and provide the two named secret references.
It follows Adobe's current two-legged OAuth token shape, sends API access tokens in the
`Authorization` header, pages the read-only Programs endpoint, and enforces the documented
[five-request-per-second instance rate](https://experienceleague.adobe.com/en/docs/marketo-developer/marketo/rest/rest-api).
See Adobe's [authentication guide](https://experienceleague.adobe.com/en/docs/marketo-developer/marketo/rest/authentication)
for the tenant-side custom-service setup.

### Hand-rolled Workday path

`WorkdayRaasSource` proves the second half of the hybrid-ingestion design without dlt. Copy
`connectors/workday_raas.example.yaml`, supply your tenant/report identifiers and secret
references, then run it through the same CLI/runtime/writer path. The source owns page-number
pagination, cursor parameters, bounded backoff, response-envelope validation, and declared
BigQuery scalar casts. Its complete test suite uses an injected fake transport; no Workday
credential or employee row is stored in this repository.

### Enterprise authentication templates

`connectors/salesforce_jwt.example.yaml` is a complete read-only Accounts QueryAll slice: OAuth2
JWT bearer authentication, Salesforce's distinct authorization-server audience, a short assertion,
server-filtered SOQL, bounded streaming Bulk API 2.0 result pages, a declared raw schema, and
soft-delete visibility. Hosted replays use an inclusive `SystemModstamp` watermark and idempotent
SCD1 publication. See [`docs/salesforce.md`](docs/salesforce.md).

`connectors/servicenow.example.yaml` reads incidents through ServiceNow's Table API using OAuth2
client credentials, primitive internal values, stable offset paging, and a declared raw schema.
The first slice performs a full read and idempotent SCD1 publication; it does not claim unsafe
timestamp-plus-offset incrementality. See [`docs/servicenow.md`](docs/servicenow.md).

`connectors/odoo.example.yaml` reads Odoo 19+ contacts and companies through the current JSON-2
API using a bearer API key, bounded pages, and an inclusive `write_date` watermark. Odoo Online
requires a Custom plan for external API access; the official Odoo Community Docker image provides
a free local development target. See [`docs/odoo.md`](docs/odoo.md).

`connectors/netsuite.example.yaml` is a **simulator-validated, not NetSuite-validated** customer
SuiteQL slice. It uses bounded offset paging, stable ordering, declared schemas, and the existing
OAuth1 TBA signer. It is not part of the public `0.2.0` support surface; real-tenant acceptance and
current OAuth2 setup are gates for a future release. See
[`docs/netsuite-simulator.md`](docs/netsuite-simulator.md).

### Strict $0 BigQuery Sandbox

For evaluation without a billing account, create a
[BigQuery Sandbox project](https://docs.cloud.google.com/bigquery/docs/sandbox), authenticate
Application Default Credentials, then run the public connector:

```bash
gcloud auth application-default login
uv run dander run greenhouse_job_board --sandbox --project my-no-billing-project
```

`--sandbox` fails closed unless the Cloud Billing API explicitly reports that billing is disabled.
It creates the raw dataset without Terraform, resolves secrets from the environment only, replaces
each destination through a `WRITE_TRUNCATE` load job, and stores observed cursors in
`.dander/state.db`. Every sandbox run is a full refresh because BigQuery Sandbox does not support
DML, including `MERGE`. It does not use Secret Manager, GCS, Cloud Run, or other services whose
free tiers require a billing account. If Cloud Billing returns an authorization/API error, Dander
does nothing; enable API access or fix the caller's read permission, then retry.

The public connector, dry runs, local tests, and all fake-provider tests need no external
credentials. Harvest v3 still requires access to a Greenhouse customer account:

```bash
uv run dander run greenhouse_job_board --sandbox --dry-run --project my-no-billing-project
```

### Billing-linked hosted platform and optional cost guard

To exercise the real Secret Manager, BigQuery `MERGE`, and BigQuery watermark path, use an existing
project with billing already linked. The managed cost guard and its project-scoped budget are
optional. Google currently provides monthly free usage for
[the first 10 GiB of BigQuery storage and 1 TiB of analysis](https://cloud.google.com/bigquery/pricing),
[six active Secret Manager versions and 10,000 accesses](https://cloud.google.com/secret-manager/pricing),
and bounded [Cloud Run compute and request usage](https://cloud.google.com/run/pricing). These are
usage allowances, not a promise that the project cannot incur charges.

Create the project-scoped budget (the project filter is important):

```bash
gcloud billing budgets create \
  --billing-account="$BILLING_ACCOUNT_ID" \
  --display-name="dander-sbx-cap" \
  --budget-amount=5.00USD \
  --filter-projects="projects/$PROJECT_ID" \
  --threshold-rule=percent=0.8,basis=current-spend \
  --threshold-rule=percent=1.0,basis=current-spend \
  --notifications-rule-pubsub-topic="projects/$PROJECT_ID/topics/dander-stop-billing"
```

Follow Google's
[programmatic notification setup](https://docs.cloud.google.com/billing/docs/how-to/budgets-programmatic-notifications)
and [billing-disable tutorial](https://docs.cloud.google.com/billing/docs/how-to/disable-billing-with-notifications)
to deploy `infra/functions/stop_billing` using the topic `dander-stop-billing`. Always deploy it
with `SIMULATE_DEACTIVATION=true`, publish a synthetic over-budget event, and inspect the simulation
log before switching it to `false`. Provider-managed trigger subscription names are supported.
Then run:

```bash
export SECRET_GREENHOUSE_CLIENT_ID='projects/PROJECT/secrets/greenhouse-client-id/versions/latest'
export SECRET_GREENHOUSE_CLIENT_SECRET='projects/PROJECT/secrets/greenhouse-client-secret/versions/latest'
uv run dander run greenhouse --guarded-free-tier --project "$PROJECT_ID"
```

Before reading the secret or extracting data, Dander requires billing enabled, the named
project-scoped USD budget at or below $5, 80% and 100% current-spend thresholds, the expected
Pub/Sub topic, and at least one attached subscription. This verifies configuration metadata; it
cannot prove the subscriber's code or runtime health. Google says budgets do not cap spending,
notifications are emitted several times daily, and charges can arrive after billing is detached.
The kill switch can stop services and make resources unrecoverable. Set the budget below the
actual amount you could tolerate and use a dedicated disposable project.

New users may instead use the [$300/90-day Free Trial](https://docs.cloud.google.com/free/docs/free-cloud-features).
While the account remains a Free Trial account, Google says usage is not charged to the payment
method; manually upgrading makes overages beyond remaining credit and free allowances billable.

`dander init` owns the complete two-stage bootstrap. It creates a hardened/versioned state bucket,
adopts it into Terraform, creates the administrative identity and Artifact Registry, builds and
pushes the current runtime, then applies datasets, per-pipeline IAM/jobs/schedules/secrets, and the
safety policy from `dander.yaml`. Newly generated projects use the ordinary hosted path without the
optional managed cost guard:

```yaml
platform:
  region: us-central1
  bigquery_location: US
  runtime:
    cpu: 1
    memory: 512Mi
    timeout_seconds: 300
    max_retries: 1
    batch_rows: 10000
  safety:
    require_guarded_free_tier: false
```

These repository-owned values configure every hosted job. `batch_rows` bounds both hosted SCD1
extraction batches and BigQuery writer requests. Sandbox replacement also consumes the endpoint
as bounded batches through a run-scoped staging table. When guarded free tier is required,
initialization rejects a disabled cost guard and hosted jobs receive
`--guarded-free-tier`. The `--region`, `--bigquery-location`, `--runtime-*`, and guarded-free-tier
override flags take precedence only when explicitly supplied. The cost guard defaults to enabled
when `require_guarded_free_tier` is true and disabled when it is false; explicit cost-guard flags can
override that default when the combination is valid.

```bash
uv run dander init \
  --project my-gcp-project \
  --failure-alert-email operator@example.com \
  --github-repository owner/repository \
  --apply
```

The state bucket defaults to `<project>-dander-state`; the active `gcloud` user becomes the
approved administrator; operator-only stage-zero artifacts default to `~/.dander/<project>` and
remain outside the repository. Terraform never receives secret values. Add each named value after
bootstrap with `gcloud secrets versions add`, then execute a paused pipeline manually before
enabling its schedule. `--failure-alert-email` is an operator input rather than a manifest field,
so personal addresses stay out of public repositories; repeat it on later reconciliations to retain
the email channel and per-pipeline Cloud Run failure policies. Plan-only mode remains available for
established environments but requires an existing backend, bootstrap identity, and immutable
`--container-image`.

The granular `init-admin-*` and `init-platform-*` commands remain available for operators who need
to review/apply each identity boundary separately. The normal path is the single command above.
The approved administrative identity is deliberately separate from runtime identities; only it
can provision project resources. Guarded installations additionally use it to delegate each
runtime's read-only billing visibility.
The default unguarded path does not request billing-account IAM or grant runtime billing/Pub/Sub
guard permissions. Dander does not manage, limit, or prevent cloud spending in that configuration.
To opt into the managed guard, set `require_guarded_free_tier: true` and pass
`--billing-account ABCDEF-123456-ABCDEF`; the caller then needs the additional billing-account
permissions required for the reviewed IAM and budget plan.

For an established environment, the equivalent explicit plan is:

```bash
uv run dander init \
  --project my-gcp-project \
  --state-bucket my-existing-tfstate-bucket \
  --bootstrap-service-account dander-bootstrap@my-gcp-project.iam.gserviceaccount.com \
  --container-image us-central1-docker.pkg.dev/my-gcp-project/dander/dander@sha256:DIGEST \
  --config dander.yaml \
  --github-repository owner/repository
```

The image must use an immutable SHA-256 digest. `dander.yaml` declares every additive pipeline,
including connector, transform roots, schedule, and secret references. Secret Manager containers
and per-pipeline runtime access are managed by Terraform, but secret values never enter the
manifest or Terraform state.
GitHub Actions authenticates through repository/ref-constrained OIDC rather than a downloaded key.
Set `publish_dataplex: true` only on pipelines that should store catalog aspects; it enables the
API and IAM required for that potentially billable operation.
The optional integrated cost guard creates the project budget, Pub/Sub wiring, and Gen 2 function in
simulation mode. Live billing detachment requires the additional `--live-cost-guard` flag and is
called out in the apply confirmation. Function deployment uses billable Cloud Build, Cloud Run,
Storage, and Artifact Registry services; free allowances do not make this a hard $0 guarantee.

### Additive hosted pipelines

The tracked `dander.yaml` runs Greenhouse and HubSpot as separate pipelines. Each pipeline receives
its own Cloud Run Job, Scheduler trigger, runtime identity, scheduler identity, secret bindings,
model selection, and pause policy. They share the immutable image and BigQuery datasets. Adding a
pipeline never repurposes another pipeline's job.

```bash
uv run dander validate
uv run dander run greenhouse_jobs --dry-run --project my-gcp-project
uv run dander run hubspot_companies --dry-run --project my-gcp-project
```

Provision or reconcile both pipelines from the manifest:

```bash
uv run dander init --project "$PROJECT_ID" --apply
gcloud run jobs execute dander-hubspot-companies --region=us-central1 --wait
```

After a new pipeline's manual ingestion, transform tests, and registry compilation succeed, set its
`paused` field to `false`, review a fresh saved plan, and apply that exact plan. The image
repository deletes untagged images after one day and retains the three most recent versions. A
small number of Scheduler jobs and Cloud Run executions may fit current free allowances, but those
allowances are not a hard spending cap. The guarded CLI preflight and budget kill switch remain
available through the explicit safety opt-in described above.

### Declared raw schemas

Every endpoint used by a pipeline in `dander.yaml` declares its complete raw BigQuery schema in
the connector. The declaration is recursive and supports `NULLABLE`, `REQUIRED`, and `REPEATED`
fields, including nested `RECORD` fields:

```yaml
endpoints:
  - name: companies
    path: /crm/v3/objects/companies
    primary_key: [id]
    raw_schema:
      - name: id
        type: INT64
      - name: properties
        type: RECORD
        fields:
          - name: name
            type: STRING
```

Before loading, Dander recursively rejects undeclared fields and invalid structural or scalar
types, fills missing nullable fields with `null`, and fills missing repeated fields with `[]`.
An empty first extraction creates the raw table directly from the declaration, so hosted sources
do not need synthetic seed rows.

Hosted SCD1 execution compares the declaration with the deployed table before loading. It may add
only missing, explicitly declared top-level `NULLABLE` fields. New nested fields, deployed-only
fields, type changes, mode changes, and removals fail before a load begins. Tables created by an
older inference-based release may therefore need an operator-reviewed migration or rebootstrap;
Dander will not guess a destructive conversion. Running a connector directly without
`dander.yaml` may still omit `raw_schema` for compatibility, but that inference path is deprecated.

### Concurrency and cursor safety

Every named pipeline acquires one exclusive lease before extraction. Hosted runs keep that lease
in `dander_meta._dander_leases`; sandbox runs use `.dander/state.db`. A second invocation records a
terminal `skipped` run instead of overlapping the active owner. Heartbeats renew the lease, and a
run that cannot renew fails closed before its next write, transform, or metadata publication.

Each successful acquisition receives a monotonically increasing fencing token. BigQuery DML
finalizers conditionally update the matching pipeline ID, run ID, and token inside the same
transaction as target mutation; a read-only lease check is not sufficient. Cursor commits use
compare-and-set against the watermark read before extraction and perform that same fenced lease
touch in hosted execution. A stale run can therefore neither publish a DML finalizer nor advance a
newer run's cursor. Sandbox replace remains atomic but is not claimed as transactionally fenced
cloud publication.

### Build and test SQL models

Every SQL model has a YAML sidecar that defines its materialization, catalog metadata, columns, and
generic tests. Dander validates the complete project, resolves `ref()` dependencies, orders models,
compiles one read-only BigQuery query per model, materializes views or tables, and then runs the
declared assertions:

```bash
uv run dander build \
  --project "$PROJECT_ID" \
  --select stg_greenhouse__jobs \
  --guarded-free-tier

uv run dander test \
  --project "$PROJECT_ID" \
  --select stg_greenhouse__jobs \
  --guarded-free-tier
```

Repeat `--select` to build multiple roots; their model dependencies are included automatically.
Omit it to build every model. References beginning with `raw_` resolve by convention to
`raw.<remaining_name>`; other references must name a discovered model. Unknown references, cycles,
missing/invalid sidecars, non-query SQL, and unsupported incremental materializations fail before
the first BigQuery query. Generic tests currently support not-null, unique, accepted-values, and
relationships.

### Inspect the metadata spine

Every named `dander run` atomically replaces its pipeline snapshot in
`dander_meta._dander_catalog` after transforms and tests pass. The snapshot contains source
endpoints, models, columns, lineage, tests, and governed metric calculations; the same run writes
its complete lifecycle outcome to `dander_meta._dander_runs`.

```bash
uv run dander metadata list --project "$PROJECT_ID"
uv run dander metadata show published_job_count --project "$PROJECT_ID"
uv run dander metadata lineage stg_greenhouse__jobs --project "$PROJECT_ID"
uv run dander metadata metrics --project "$PROJECT_ID"
uv run dander metadata runs --project "$PROJECT_ID"
```

The same model sidecar can also project into a deterministic file and optional Dataplex Knowledge
Catalog aspects:

```bash
uv run dander catalog \
  --project "$PROJECT_ID" \
  --select stg_greenhouse__jobs \
  --output .dander/catalog.json
```

Local compilation is the default. `--publish-dataplex` explicitly attaches overview, contacts,
schema, and generic system aspects to the corresponding BigQuery entry; it can be combined with
`--guarded-free-tier`. Publication never deletes unrelated aspects. Google currently makes
Knowledge Catalog API calls free but charges for stored aspect metadata, so cloud mutation is not
implicit. See [Knowledge Catalog pricing](https://cloud.google.com/products/knowledge-catalog/pricing)
and [Dataplex aspect management](https://docs.cloud.google.com/dataplex/docs/enrich-entries-metadata).

Current v0 limits are collected in the
[known-limitations page](https://github.com/harrisonoconnorhover/dander/blob/main/docs/known-limitations.md).
The tracked implementation ledger remains in [`docs/spec-alignment.md`](docs/spec-alignment.md).

## The agent workforce & the `/feature` workflow

Features are built by a workforce of agents defined in `.claude/` — the `feature` workflow runs the
loop **Product → Design → Code → PR-Review**, looping a ticket back to Code with an addendum until
it passes review. See `CLAUDE.md` for the full picture.

**First, register it.** `.claude/agents/`, `.claude/workflows/`, and `.claude/commands/` are loaded
only at **Claude Code startup**. After cloning (or after editing anything under `.claude/`),
**restart Claude Code in the project root** so `/feature`, the agents, and the `feature` workflow
become available.
**Run "/config workflows=true" in a Claude chat window to enable it for that session.**

**Then run it** (any of these — it costs tokens, so each run is an explicit opt-in):

```text
/feature Add an ApiKeyBasic auth strategy and wire GcpSecretStore
```
```text
(or just ask Claude in chat)   run the feature workflow with: <describe the feature>
```
```bash
# headless / scripted, from a terminal:
claude -p --permission-mode acceptEdits "run the feature workflow with args: <describe the feature>"
```

It writes tickets to `tickets/` (lifecycle `open → in-design → in-code → in-review → done`),
implements + reviews each until PASS, and leaves the code + tests in your working tree.

## Watching workflows in real time

A workflow run spawns many background agents. `scripts/watch_workflows.py` is a dependency-free
(stdlib-only) live dashboard — run it in a **separate terminal** while a workflow is going:

```bash
python3 scripts/watch_workflows.py          # live dashboard, refresh every 2s
python3 scripts/watch_workflows.py --all    # include finished / idle runs
python3 scripts/watch_workflows.py -n 5     # refresh every 5s
python3 scripts/watch_workflows.py --once   # print one snapshot and exit
```

It auto-discovers **all** runs across sessions (so it handles several concurrent workflows), and
shows each run's agents with their role, ticket, and live PASS/FAIL verdicts:

```text
● wf_020b226b-07f  RUNNING  elapsed 13m48s  agents 7 done
   ✓ product       —         2 ticket(s)
   ✓ design        DANDER-2  design ready
   ✓ code-python   DANDER-2
   ✓ pr-review     DANDER-2  PASS
   ▸ pr-review     DANDER-3  working…
```

## Status

Runnable ingestion v0: the Greenhouse → BigQuery production SCD1 path, strict no-billing sandbox,
and billing-linked guarded preflight, plus audited secret resolution, watermark state, dry-run
planning, and BigQuery Terraform bootstrap are implemented and unit-tested. The limits above still
make this **unsuitable for production**. The named HR, compensation, and customer systems describe
possible connector categories; they do not imply that this repository came from, connects to, or
contains data from an existing company. Normal provenance, licensing, and privacy review still
applies before adding employer-owned code or non-public data.

For the exact current branch, validation, deployed-sandbox, and next-session state, see
[`docs/session-resume.md`](docs/session-resume.md).

## License

Apache-2.0.
