---
id: DANDER-127
title: Add the Dander control-service projection
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-121, DANDER-126]
created: 2026-08-13
---

## Context

Long-running Dander Control API services have current deployment needs distinct from job launchers
and from Druff's static artifact.

## Acceptance Criteria

- [x] Add immutable resolved request/template contracts for image/command/port/probes/resources/
      scaling/shutdown, environment, secret refs, identity, ingress/origins, GraphStore,
      observability, and rollback digest.
- [x] Add an internal service provider kind/factory with lazy selected-provider loading.
- [x] Keep job launcher contracts and projections byte/semantically unchanged.
- [x] Keep Druff `StaticAssetBundle` as a separate deployment input with digest, entrypoint,
      bootstrap digest, and security headers.
- [x] Deterministic projection and fail-before-provider-access tests pass.

## Design

Provider TLS, networking, IAM, load balancers, and native resource IDs remain in provider modules;
this is not a universal service framework.

## Implementation Notes

- Added a frozen `ResolvedControlServiceRequest`, deterministic `ControlServiceTemplate`, template
  factory protocol/runtime, and narrow validated component contracts adjacent to the unchanged job
  launcher boundary.
- Reused `HostedOIDCDeploymentInput` as the only trust/origin source and derived the canonical
  external host, port, and OIDC-config command arguments from validated fields.
- Added a closed credential-free GraphStore binding union for local, GCS, S3, Azure Blob, and OCI
  Object Storage runtime locators. Provider networking, IAM, SDK clients, credentials, and native
  resource IDs remain outside this contract.
- Added one bounded typed JSON startup seam. The request derives `--graph-store-config` with an
  exact absolute path; `control serve` rejects malformed or extra fields before adapter access and
  instantiates only the selected binding arm. D7 owns provider-side delivery of that non-secret
  file.
- Added the internal `service` provider kind without a built-in provider implementation; D7 owns
  those provider modules. A fake factory proves parse-before-load and selected-only loading.
- Added a separate immutable `StaticAssetBundle` for Druff digest/entrypoint/bootstrap/header
  identity without forcing static assets into service or launcher contracts.

## Review Log

The pre-implementation adversarial review found that an independent origin list and prefix-only
command could validate a service that bound loopback, rejected startup without OIDC, or silently
used the local GraphStore. The implementation instead reuses the existing frozen hosted OIDC
input, derives canonical external command arguments and origins, and uses only the requested
closed credential-free GraphStore locator arms. It adds no provider service implementation.

The completion review found that the first implementation declared the locator but did not connect
it to `control serve`, allowing a cloud request to start on the default local store. The focused
correction adds the typed config path, bounded closed parser, selected-adapter factory, and tests
covering all five arms before provider access. No third adversarial pass was requested under the
two-pass review limit.

Protected PR CI run `31806362480` passed Python quality, Terraform quality, secret scan,
distribution install, and container build/scan on implementation commit `395a18d0`.

The first immutable package containing this boundary is `dander-platform==0.9.0rc20`, published
from protected-main commit `75c5654e95439eaf18e90fbacc849799f4fe42b6` at tag `v0.9.0rc20` by
trusted-publishing run `31815063258`. The public wheel/source hashes matched the workflow artifacts,
and fresh no-cache PyPI-only CLI, scaffold, project, and Terraform validation passed outside every
checkout. This package release does not publish a current service image, implement a provider
service projection, or promote support.
