# Morning Handoff

## Finished

- Added a public, read-only optional capability contract for targeted lookup, cheap count, and
  connection testing.
- Added `SourceCapabilities`, which detects and invokes those operations on built-in or
  independently installed plugin sources with clear unsupported/result-contract errors.
- Added registry integration without changing `Source`, `ConnectorPlugin`, or plugin API v1.
- Documented the plugin-author contract, deferred mutations, and Josh Wagner's originating commit.

## Try It

```python
capabilities = registry.build_capabilities(source_config, auth)
if capabilities.supports(ConnectorOperation.TEST_CONNECTION):
    status = capabilities.test_connection()
```

## Checks

- Focused Ruff, mypy, and 18 tests passed.
- Full Ruff and strict mypy passed; all 728 tests passed.
- Wheel and sdist inspection passed.
- Outside-checkout wheel installation and public capability imports passed.

## Decisions

- Optional operations are detected structurally on the `Source` returned by the existing factory.
- The initial contract is read-only and does not bump connector plugin API v1.
- Deleted-record feeds and provider mutations remain separate product decisions.

## Remaining

- Push the focused branch, open its protected-main PR, and let CI repeat validation.
- Add the narrow connector inspect/check CLI path after this contract merges.
- Implement the first real capabilities in the independently packaged Salesforce connector.
- Add safe configurable operations through graph transforms, not raw ingestion.

## Review First

- `src/dander/ingestion/capabilities.py`
- `src/dander/plugins/registry.py`
- `tests/ingestion/test_capabilities.py`
