# Upgrades and safe reruns

Dander upgrades are exact-version, source-free, and plan-reviewed. Only the latest patch in the
current public minor is supported. Preserve your project directory, remote Terraform state, Secret
Manager values, raw data, watermarks, and run history.

The examples assume the operator has set `DANDER_PROJECT` and `DANDER_ALERT_EMAIL` as described in
the [hosted quickstart](getting-started.md). A guarded installation must also retain its existing
`DANDER_BILLING_ACCOUNT`; the standard unguarded path does not require one.

## Upgrade the CLI and project image

```bash
export DANDER_TARGET_VERSION="0.7.0rc1"
uv tool install --force "dander-platform==$DANDER_TARGET_VERSION"
dander --version
```

Set every hosted pipeline's `paused` field to `true`, update the exact `ARG DANDER_VERSION=` value
in the project Dockerfile, then run `dander validate`. Do not use an unpinned package or mutable
container tag.

Build and push the source-free project image through the bootstrap identity:

```bash
dander image-publish \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_PROJECT-dander-state" \
  --failure-alert-email "$DANDER_ALERT_EMAIL"
```

Copy the immutable digest from the output and run the complete `init-platform-plan` command Dander
prints. Image publication uses the bootstrap service account; the operator needs Token Creator on
that account, not Project Owner or direct Artifact Registry administration.

## Review and apply the exact plan

```bash
dander init-platform-plan \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_PROJECT-dander-state" \
  --bootstrap-service-account "dander-bootstrap@$DANDER_PROJECT.iam.gserviceaccount.com" \
  --container-image "$DANDER_IMAGE" \
  --failure-alert-email "$DANDER_ALERT_EMAIL"

terraform -chdir=infra show -no-color dander-bootstrap.tfplan

dander init-platform-apply \
  --project "$DANDER_PROJECT" \
  --state-bucket "$DANDER_PROJECT-dander-state" \
  --bootstrap-service-account "dander-bootstrap@$DANDER_PROJECT.iam.gserviceaccount.com"
```

If `platform.safety.require_guarded_free_tier` remains `true`, also pass the installation's existing
`--billing-account "$DANDER_BILLING_ACCOUNT"`. Do not change that safety setting merely to simplify
an upgrade.

Reject a plan that deletes or replaces a dataset, secret, state bucket, runtime identity, or an
unrelated job. Expected upgrade changes are the immutable Cloud Run image and the intentional
scheduler pause.

## Prove, restore, and rerun

Execute each paused job manually, inspect `dander metadata runs`, and verify row counts, tests,
watermarks, and catalog assets. Run each job a second time to prove idempotence. Do not start a
second execution while one is active; a recorded `skipped` overlap is not a failure and should not
be immediately retried.

After every pipeline passes, restore the tracked `paused` values, build the resulting project image,
repeat the reviewed plan/apply sequence, and run one final plan that reports no changes.

Use [rollback.md](rollback.md) if the candidate cannot pass its manual smoke run. Rollback reuses a
known-good immutable digest and never edits Terraform state, leases, watermarks, or staging by hand.

On an alert, inspect the Cloud Run execution and Dander run ledger first. Rerun once only after the
prior execution is terminal. Never edit `_dander_leases`, `_dander_watermarks`, run-scoped staging,
or Terraform state by hand. A cleanup, lease, cursor, schema, or unexplained-drift problem is a
defect in the current supported release.
