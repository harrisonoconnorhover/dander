---
id: DANDER-91
title: Prepare AWS secrets and keyless Fargate runtime identity
status: in-review
component: deployment
epic: cloud-portability
depends_on: [DANDER-90]
created: 2026-08-08
---

## Context

The Fargate projection names task-role and Google workload-federation intent, but runtime startup
must prepare keyless Google credentials and AWS secret resolution before any provider client is
constructed. This slice keeps those adapters lazy and does not provision or advertise Fargate.

## Acceptance Criteria

- [x] AWS Secrets Manager registers lazily and does not load boto3 before real access.
- [x] Secret references require a full, region-matching ARN and text value.
- [x] Environment indirection and value-free access auditing remain available.
- [x] Fargate accepts only temporary ECS task-role credentials from the fixed link-local endpoint.
- [x] Static AWS credential environment variables and stale sessions fail closed.
- [x] Google external-account configuration contains no secret and limits impersonation to 600s.
- [x] Runtime identity preparation occurs before provider-client construction.
- [x] Identity failures produce sanitized authentication telemetry.
- [x] Fargate execution remains bounded while ECS credentials are not renewable in-process.
- [x] Focused tests, lint, formatting, and strict typing pass.
- [x] Full validation and isolated GCP no-drift pass.
- [ ] Protected CI passes.

## Design

Reuse the Phase 1B ECS credential adapter in core, expose only one launcher identity hook, and keep
AWS-specific code behind lazy provider selection. Do not add AWS Terraform, scheduler lifecycle,
profile support, or a public support claim in this PR.

## Review Log

All 933 tests, lint, formatting, typing, distribution, dependency, container, security, and
Terraform checks passed. The isolated GCP plan reported exactly `No changes`, both schedules
remain paused, and read-only AWS identity confirmed account `184463061564` in `us-east-1`.
Protected review remains pending.
