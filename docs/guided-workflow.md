# Guided workflow integration candidate

The October 1 candidate adds a single Druff journey for configuring a graph, reviewing its
changes, running the reviewed saved revision, and understanding the recorded result. Selected-date
repair is available for eligible BigQuery outputs executed by a single Cloud Run task.

This is an experimental integration candidate. Public PyPI Dander remains `0.9.0rc20`; existing
production identity-provider, multi-replica, provider-scale, and broader release gates remain open.

## Install the exact backend

The immutable [RC34 release](https://github.com/harrisonoconnorhover/dander/releases/tag/v0.9.0rc34)
contains the wheel, source archive, and `SHA256SUMS`. Install the wheel into a separate environment
with the extras needed by the PostgreSQL/GCP profile:

```bash
uv venv --python 3.12 .venv-rc34
uv pip install --python .venv-rc34/bin/python \
  'dander-platform[postgres,bigquery,gcp] @ https://github.com/harrisonoconnorhover/dander/releases/download/v0.9.0rc34/dander_platform-0.9.0rc34-py3-none-any.whl#sha256=d37c1d91c15cca9ce3d080af6dec384f7cd9cbc521dea253e10dc44772d35bad'
.venv-rc34/bin/dander --version
```

Use the prerequisites and flags in [Control profile preparation](control-profiles.md) to register
an already-deployed graph. For this artifact-pinned installation, extract the source archive from
the same RC34 release and invoke its preparation script with the installed wheel's interpreter:

```bash
.venv-rc34/bin/python /path/to/rc34-source/examples/control/prepare_cloud_run.py \
  --config /path/to/project/dander.yaml \
  --pipeline greenhouse_jobs_graph \
  --gcp-project-id your-existing-project \
  --image 'us-central1-docker.pkg.dev/your-existing-project/dander/dander@sha256:YOUR_DIGEST' \
  --output-dir /path/to/private/control \
  --schema control_example
.venv-rc34/bin/dander control serve --profile /path/to/private/control/control.yaml
```

Use these commands in place of the linked source-checkout `uv sync` and `uv run` commands, so
Control keeps using the verified wheel. Set `DANDER_CONTROL_DATABASE_URL` through your existing
secret mechanism first. Credentials, deployed jobs, and the dedicated database still belong to
that profile. The guided interface does not provision infrastructure or discover secrets.

RC34 is not on PyPI. Its generated project Dockerfile still expects a PyPI package; use the named
candidate runtime image for this integration instead of building that default Dockerfile.

Use RC34 for the Control process. The paired Linux/AMD64 **worker** remains RC33 because the
correction changes only Control reconciliation, not the native execution contract. Do not use the
worker image to start Control; its bundled Control command still contains the RC33 defect.

The public worker image is:

```text
ghcr.io/harrisonoconnorhover/dander@sha256:89bd242e55d6e5ca37394ce81187f7d036e26b670c22f0ed5805a3c1df9447dc
```

Use this digest as the base for your project's configuration image, then deploy that image through
the existing project workflow. The Control preparation example consumes the already-deployed job's
exact configuration and image; it does not build or deploy a job.
The readable tag is `0.9.0rc33-amd64`; pin the digest for execution. The image passed native startup,
shutdown, provider-import and repair-contract checks. Its public manifest matches the local
verified build, and an anonymous pull by digest reported `dander 0.9.0rc33`.
No fixed HIGH/CRITICAL vulnerabilities were found. A separate local secret-pattern
scan matched only documented examples in the Oracle SDK; the repository secret scan passed.

## Open the guided interface

Druff `0.2.0-rc.1` comes from protected source
`eabceb21d7b1b38fcbccdf34f07772e89ea52f6a`.
Its [exact-main CI](https://github.com/harrisonoconnorhover/druff/actions/runs/36877515144)
passed frontend checks, contract drift, browser journeys, both architecture scans, runtime
checks, and build reproducibility.

The published multi-architecture client image is:

```text
ghcr.io/harrisonoconnorhover/druff@sha256:6aade12c399a67c604577e4a022af6dff22db7764626dd9a7a4f5cea1a2ceac1
```

The readable tag is `0.2.0-rc.1`. The verified OCI bytes were copied without rebuilding, and
anonymous registry access confirmed that exact index. Both AMD64 and ARM64 images passed
source-free, non-root, read-only runtime, route/header, vulnerability, and secret checks.
Existing `active` and `rollback` aliases were preserved; this is a separately selected candidate.

This journey is in Druff's **hosted mode**. Serve the candidate with the same-origin
`/bootstrap.json` generated from the matching RC34
[hosted OIDC deployment input](control-contracts.md#hosted-oidc-boundary). Regenerate that public
descriptor from the existing deployment input when upgrading; an older contract digest will be
rejected. The descriptor contains connection settings, not credentials.

Without that descriptor, Druff opens its existing loopback/offline editor. Starting the local
Control profile alone does not activate the hosted journey. Use the existing configured OIDC
deployment and its login flow; this candidate does not create an identity provider.
The [Druff hosted-interface instructions](https://github.com/harrisonoconnorhover/druff/blob/eabceb21d7b1b38fcbccdf34f07772e89ea52f6a/README.md#hosted-interface)
describe serving the static image and descriptor together.

## Review and run

1. Sign in to the configured hosted Druff interface and open a saved graph.
2. Configure its source, transformations, and outputs, then choose **Preview changes**.
3. Review the change and affected-output summary. Unknown rows and cost remain unknown.
4. Choose **Save reviewed changes**, then **Run reviewed graph**. Further edits or a conflicting
   saved revision require a new preview.
5. Read the recorded result and suggested available actions. Technical controls, logs, cancellation,
   and replay remain available in the existing details.

For an eligible saved graph, **Repair selected output dates** previews the date interval before
submission. It rebuilds from currently retained raw data, preserves surrounding output dates, and
leaves source ingestion progress unchanged. Read [output repair](output-repair.md) for UTC
boundaries, existing-output requirements, key-collision rejection, and per-output transactions.

## Fixed producer identity

- Protected source: `6964d63f4e9e8eab15ce63e4a1c65efb5268c2d0`.
- [Exact-main CI](https://github.com/harrisonoconnorhover/dander/actions/runs/36874195042): passed.
- Wheel SHA-256: `d37c1d91c15cca9ce3d080af6dec384f7cd9cbc521dea253e10dc44772d35bad`.
- Source archive SHA-256: `3f5f812e391775149c611d868540f452efbfe42070702542eb4985800083783e`.
- Control bundle SHA-256: `a28316b7e5158e0520fe1c24d59885083714f47b67aa396892fa9742060fb279`.

The public download matched the tested wheel, GitHub release attestations verified both archives,
and a fresh installed environment verified all 45 packaged contract files and both curated pins:
Salesforce `0.3.2` and ServiceNow `0.2.3`. Their combined 116 tests passed against the published
wheel. These connector checks do not claim a new live source run.

RC34 corrects a defect found during the first native repair check: Cloud Run resolved the
configured image index to its AMD64 image, and RC33 Control rejected that identity during
terminal reconciliation even though the data operation succeeded. RC34 verifies the relationship
through Artifact Registry metadata before accepting the resolved image. The Control principal
needs `artifactregistry.dockerImages.get` on the existing repository; see
[profile permissions](control-profiles.md). The fixed producer passed 2,528 tests and strict
typing across 516 files on its exact merged source revision.

## Native repair verification

On October 1, the published RC34 Control wheel and the RC33 worker completed the synthetic run
`run-ec3ecadaeb48d3cca4ccf537`. Its window included September 10 and 11 and excluded September 12.
The task corrected an existing row, inserted another retained row, and removed obsolete selected
data. Control recorded two output rows written, four affected, zero ingestion rows, two measured
operations, and zero retries. Surrounding output rows, raw data, and ingestion progress were
unchanged. A separate direct transaction rejected a key crossing the date boundary without
changing the output.

Repeating the request returned the same run, with exactly one native execution. Control restored
the original Job arguments and environment and reported success with results at 14:33:41 UTC.
Druff's actual production client read that run and its explanation successfully at 14:34:17 UTC.
Browser journey tests used synthetic OIDC/API fixtures; this client observation does not turn
those fixtures into production identity-provider evidence.

The temporary Job, dataset, image index, and both image children were verified absent at
14:36:32 UTC, before the original 14:54:49 cleanup deadline. Both proof environments' local
Control processes, PostgreSQL containers, watchdogs, and task credential files were also removed.
The USD 1 qualification reservation remains held inside the USD 21 aggregate October reservation
while provider billing settles; it is a conservative reservation, not an observed final charge.

This is a small, named synthetic repair proof. It does not establish production source accuracy,
large-table cost, other execution backends, or atomic publication across multiple outputs.
