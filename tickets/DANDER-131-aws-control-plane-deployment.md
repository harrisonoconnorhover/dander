---
id: DANDER-131
title: Add the AWS hosted Control-plane deployment
status: in_progress
component: python
epic: druff-control-plane
depends_on: [DANDER-130]
created: 2026-08-14
---

## Context

D7 continues with a separate experimental AWS profile after the accepted GCP proof. The profile
must reuse the D6 service, OIDC, and S3 GraphStore contracts, use only the established short-lived
deployment role, and keep disposable resources in an isolated state prefix.

## Acceptance Criteria

- [x] Extend the existing AWS deployment role with action-bounded, name/tag-scoped authority for
      disposable D7 ECS, ELBv2, CloudFront, security-group, S3, and versioned-state cleanup.
- [x] Render one closed immutable non-secret input into exact active/rollback AWS values and aligned
      Control OIDC, S3 GraphStore, Druff bootstrap, and provider deployment files.
- [x] Add a separately packaged partial-backend Terraform root with one provider-issued HTTPS
      origin, distinct keyless tasks, content-bound startup task revisions, and private versioned
      GraphStore storage.
- [x] Add deterministic backend-free preflight, bounded read-only live verification, focused
      Python/Terraform tests, and protected-CI coverage.
- [ ] Qualify synthetic OIDC, canonical browser persistence, S3 conflicts/replay/restart/cleanup,
      immutable digest rollback/restore, no-change plans, and retained AWS/GCP no-drift.

## Design

The application root cannot grant its own deployment authority or fall back to account-admin
credentials. A focused prerequisite therefore adds a second inline policy to the retained
short-lived deployment role. Provider creation actions remain enumerated; mutable resource access
is constrained by D7 names, tags, and account/region ARNs wherever AWS supports that boundary.
Version-list and version-delete permissions cover both the retained state prefix and disposable
D7 buckets so exact cleanup can inspect and remove noncurrent generations. Retained-state version
access is fixed to `dander/d7/control-plane/`; the later application root must use that exact
backend prefix and cannot inspect or delete any unrelated state history.

CloudFront is the single public browser/API origin. Its mutable API and probe behaviors have zero
TTL, forward every viewer header and query string but no cookies, and route through a public ALB
whose ingress is limited to AWS's managed CloudFront origin prefix list. The static behavior keeps
minimum TTL zero so the unchanged Caddy response headers remain authoritative. Both CloudFront and
ALB access logging remain disabled at this boundary.

Startup inputs contain public OIDC coordinates or credential-free locators, never secrets. Rather
than adding an S3 config bucket and another read identity, the exact base64 content is carried in a
sensitive task-definition revision. A fixed root init process from the same immutable Dander image
writes mode-0444 files to an ephemeral volume and must succeed before the UID/GID 65532 app mounts
that volume read-only. Long-running containers have read-only roots, dropped capabilities, and a
writable ephemeral `/tmp`. Only Control's distinct task role can access the graph prefix; Druff's
task role is empty.

## Review Log

The adversarial pre-review accepted CloudFront plus a public ALB as the smallest provider-issued
HTTPS boundary without a custom domain. It blocked implementation because the existing deployment
role could not create or fully clean that profile. This prerequisite supplies only the missing
actions and retains the exact-operator, one-hour role boundary. The same review also requires the
later profile to disable API caching, explicitly forward required headers and query strings, route
both probes to Control, and give read-only Fargate tasks exact successful config initialization
plus writable ephemeral `/tmp` mounts.

The completion review found that the first prerequisite draft had added version-history actions
to the pre-existing bucket-wide state statements. The correction removes that authority and adds
separate list/get/delete-version grants constrained to the fixed D7 backend prefix. Focused tests
assert both the `s3:prefix` condition and object-ARN boundary.

The application implementation keeps the review's successful-init and writable-runtime boundary
but removes the proposed provider config fetch entirely. Because the four startup files are
non-secret, binding them directly into the immutable task definition is smaller and avoids giving
an init image any S3 config-read authority. The same accepted Dander image performs the bounded
write, so the profile introduces no additional executable supply-chain input.

The completion review found that explicit container CPU reservations exceeded both Fargate task
allocations once the sequential initializer was included. The correction removes those optional
container fields, relies on the already-valid task-level sizes, and decodes both definitions in a
focused Terraform assertion so no reserved sum can meet or exceed its task allocation.

Protected CI then surfaced five generic static-analysis warnings for the reviewed profile shape.
Resource-local Trivy exceptions now document why this bounded acceptance profile uses AWS-owned
SSE-S3, HTTPS-only public egress, a CloudFront-prefix-restricted public ALB with an HTTP origin, and
no WAF. The correction suppresses only those exact checks and adds no broader ignore policy.
