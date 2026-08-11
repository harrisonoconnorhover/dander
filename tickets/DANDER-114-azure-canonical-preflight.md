---
id: DANDER-114
title: Preflight the canonical Azure acceptance profile
status: in_progress
phase: 6
---

# DANDER-114 — Preflight the canonical Azure acceptance profile

## Goal

Give the operator one read-only, fail-closed command that verifies the exact named Phase 6
Azure/Snowflake/PostgreSQL/no-catalog/Key-Vault composition before any job execution.

## Acceptance

- [x] Other platform compositions, Snowflake key-pair auth, and missing OAuth/DSN secret bindings
  fail before provider binding.
- [x] Existing deployment verification checks the exact immutable image and Azure resources first.
- [x] A separate Key Vault list requires every manifest-declared secret name to exist and be
  enabled.
- [x] The command does not read values, expose unrelated vault entries, mutate Azure, or ask for a
  paid-operation confirmation.
- [x] Focused provider and CLI tests cover success, missing/disabled secrets, composition rejection,
  and sanitized output.
- [ ] Protected CI passes and this focused preflight merges before live Azure acceptance.

## Boundary

This is local construction for a later read-only provider check. It does not register a provider,
create a resource, read a secret value, copy an image, start a job, query a warehouse, or authorize
spending. Version rotation and runtime use remain live acceptance work.
