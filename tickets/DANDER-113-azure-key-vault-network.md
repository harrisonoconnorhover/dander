---
id: DANDER-113
title: Give Azure jobs an explicit Key Vault network path
status: done
phase: 6
---

# DANDER-113 — Give Azure jobs an explicit Key Vault network path

## Goal

Correct the pre-live assumption that Container Apps can use Key Vault's trusted-service firewall
bypass. Keep the vault default-deny while admitting the exact network path used by the named Azure
profile.

## Acceptance

- [x] Key Vault references fail planning when no Container Apps infrastructure subnet is supplied.
- [x] The vault admits the exact supplied subnet and the reviewed operator IP.
- [x] The inapplicable Azure trusted-service bypass is disabled.
- [x] Read-only deployment verification rejects an Azure Key Vault profile with no virtual-network
  rule.
- [x] Provider-mocked Terraform and focused Python tests cover the correction without an Azure
  mutation.
- [x] Protected CI passed and the focused correction merged as PR #193 before any Azure plan was
  applied.

## Boundary

The supplied subnet must already be delegated for Container Apps and have the
`Microsoft.KeyVault` service endpoint. Dander does not create or modify that subnet in this slice.
This ticket registers no provider, creates no resource, writes no secret, starts no job, and spends
no cloud credit.
