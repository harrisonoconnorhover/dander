---
id: DANDER-120
title: Add the GraphStore contract and local adapters
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-119]
created: 2026-08-13
---

## Context

Hosted multi-graph routing must not embed a second one-file persistence implementation.

## Acceptance Criteria

- [x] Define list/get/create/put/delete semantics with strict project/graph identifiers, bounded
      pagination/documents, opaque revisions, separate canonical content hashes, and idempotency.
- [x] Implement in-memory and rooted local-filesystem adapters without arbitrary path input.
- [x] Preserve canonical serialization and conditional stale-write/delete rejection.
- [x] One conformance suite proves create/read/list/update-conflict/restart/delete behavior.
- [x] Existing `dander graph serve --file` behavior and tests remain unchanged.

## Design

Land the port before hosted routing. Provider generations and ETags remain adapter-private opaque
revision material; cross-cloud evidence compares canonical content SHA-256 only.

`GraphStore` accepts validated `PipelineGraphDocument` values and returns immutable full records
from get/create/put, document-free summaries from list, and a deletion receipt from delete. Project
and graph identifiers use one portable lowercase ASCII grammar. Page cursors are opaque,
project-bound, bounded tokens; a page never contains graph document bytes.

Canonical bytes are UTF-8 JSON from `graph_to_payload` with sorted keys, compact separators,
unescaped Unicode, no non-finite numbers, and no trailing newline. The 5 MiB limit and portable
content SHA-256 both apply to exactly those bytes. Revisions remain separate random opaque tokens.

Create and delete keys are scoped by `(project, operation, key)`. Their request fingerprints cover
the graph and content hash or expected revision. Identical retries replay the exact first success;
reuse for another request conflicts; validation and failed preconditions do not consume a key.
The rooted filesystem adapter writes a durable pending journal before mutation and a completed
journal afterward. Restart reconciliation makes a crash at either boundary retry-safe. It stores
only deterministic paths under one operator-selected root and rejects symlink/path escape.

## Implementation Notes

- Added frozen full-record, summary-page, and delete-receipt values plus typed safe failures.
- Both adapters share validation, canonical bytes, portable hashes, cursors, limits, conditional
  semantics, and idempotency fingerprints.
- The local adapter stores deterministic private envelopes under one resolved root. Hashed journal
  filenames do not disclose mutation keys; atomic writes and directory fsyncs preserve boundaries.
- Shared tests run against both adapters. Local-only tests cover restart and simulated interruption
  after pending journal, graph mutation, and completed journal writes for create and delete.

## Review Log

### 2026-08-13 — pre-implementation adversarial review

Required list pages to use document-free summaries, specified the canonical byte encoding, and
replaced independent graph/ledger commits with a pending/completed journal plus crash-boundary
reconciliation tests.

### 2026-08-13 — completion adversarial review — PASS

No material findings after reviewing the contract, both adapters, crash reconciliation,
conformance evidence, documentation, and preserved loopback boundary.
