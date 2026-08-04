# Python release candidates

Dander publishes the `dander-platform` distribution while preserving `dander` as its import
package and CLI. Releases are immutable and come only from an exact `v<version>` tag on protected
`main`.

## Candidate gate

1. Merge the packaging commit and wait for post-merge CI, including `Distribution install`.
2. Confirm `git status --short` is empty and the tag target is the tested `origin/main` commit.
3. Confirm the GitHub `pypi` environment requires review and PyPI trusts this repository's
   `publish.yml` workflow for the `dander-platform` project.
4. Create and push the exact `v<version>` tag only after explicit publication approval.
5. Dispatch **Publish Python distribution** from that tag and approve its `pypi` environment.
6. Install the published candidate into a new environment outside a checkout and repeat
   `dander --version`, `dander new`, `dander validate`, and Terraform validation.

The workflow builds fresh artifacts from the tag, validates their identity and contents, and uses
PyPI trusted publishing. It has no long-lived package token and refuses a branch or mismatched tag.

Phase 6 is acceptance-only for product code. If any functional runtime change is required after a
candidate is published, stop the proof and publish the corrected commit as the next candidate
through this same gate. Rerun the exposing scenario and the standard source-free, Greenhouse,
HubSpot, replay, cleanup, and no-drift smoke suite. Repeat the complete live proof only when the fix
broadly changes packaging, provisioning, orchestration, state, cursors, concurrency, or cleanup.
Tests, evidence tooling, and documentation may change without a new candidate only when packaged
runtime behavior is unchanged.

## Alpha release lines

- Published minor lines contain fixes only: installation, upgrades, drift, cursor/lease
  correctness, schemas, staging cleanup, CLI accuracy, security, and documentation that blocks
  operation.
- New connectors, commands, manifest capabilities, writer modes, and subsystems enter through the
  next minor release.
- Only the latest patch in the current `0.x` minor is supported. A functional patch uses its own
  candidate, public-artifact proof, protected publication, and clean upgrade verification.
- GitHub Release notes must match `CHANGELOG.md`, identify the release as alpha, link the known
  limitations, and name the exact tag and commit.

## Initial `0.1.0` gates and post-release soak

- Before final publication, the latest approved candidate must pass the bounded retained-project
  acceptance.
- A separate operator must complete the public Greenhouse quickstart in a fresh disposable GCP
  project without a source checkout or unpublished instructions.
- The independent installation is a one-time release gate. The 30-day operator soak begins only
  after `0.1.0` is public and does not retroactively block the release.
- During the soak HubSpot and Greenhouse run daily, run history is reviewed weekly, each pipeline
  receives one documented manual rerun, and sanitized outcomes live in one operator-trial issue.
- A functional patch does not restart the whole soak, but closing it requires seven consecutive
  clean days on the newest patch. Documentation-only corrections do not affect the clock.
