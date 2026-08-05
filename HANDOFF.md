# Morning Handoff

## Finished

- Merged the read-only source capability contract through protected PR #71.
- Added `dander connector inspect` for provider-free capability discovery.
- Added `dander connector check` for an optional authenticated, record-free connection probe.
- Reused manifest plugin pins, pipeline-to-source resolution, core authentication, and secret
  references without changing normal ingestion.

## Try It

```bash
dander connector inspect PIPELINE_OR_SOURCE
dander connector check PIPELINE_OR_SOURCE
```

## Checks

- Focused Ruff, strict mypy, and 23 capability/CLI/registry tests passed.
- Full Ruff and strict mypy passed; all 733 tests passed.
- CLI help and provider-free Greenhouse inspection passed.
- Wheel/sdist inspection and outside-checkout connector CLI startup passed.
- PR #71 protected CI passed Python, 728 tests, dependency, Terraform, distribution, container,
  and secret checks.

## Decisions

- `inspect` constructs the configured source but never invokes a provider operation.
- `check` invokes only `test_connection` and displays its non-secret scalar status.
- Unsupported connectors fail clearly; the command never falls back to extracting a sample row.

## Remaining

- Push and merge the connector CLI PR through protected CI.
- Implement the three read-only capabilities in the independent Salesforce plugin.
- Publish a compatible Dander candidate before merging the plugin dependency change.
- Add safe operations through canonical graph transforms after the connector slice.

## Review First

- `src/dander/cli/main.py`
- `tests/cli/test_connector_cli.py`
- `docs/connector-plugins.md`
