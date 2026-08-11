---
id: DANDER-112
title: Bind Azure proof-secret rotation to one operator
status: done
phase: 6
---

# DANDER-112 — Bind Azure proof-secret rotation to one operator

## Goal

Allow the exact authenticated Azure plan operator to create and rotate the named profile's Key
Vault secrets without widening the Container Apps runtime identity or putting secret values in
Terraform.

## Acceptance

- [x] The root passes the authenticated Azure principal object ID into the Container Apps module.
- [x] The module grants that exact principal `Key Vault Secrets Officer` only on the deployment
  vault.
- [x] The runtime identity remains limited to `Key Vault Secrets User` for the same vault.
- [x] Provider-mocked tests distinguish the operator and runtime principals and assert both roles.
- [x] Protected CI passed and the focused correction merged as PR #192 before any Azure apply.

## Boundary

This ticket changes only Terraform construction and documentation. It does not register a provider,
assign a live role, create a vault, write a secret, run a job, or authorize spending. Secret values
remain outside plans, state, logs, and committed evidence.
