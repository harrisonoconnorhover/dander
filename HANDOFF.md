# Morning Handoff

## Finished

- Published and source-free verified Dander `0.3.0` from commit `15494be17a3b5b06d61e6f7e66685db5326262e9`.
- Added exact manifest plugin pins and explicit `dander.connectors` entry-point discovery.
- Added the public plugin API v1 contract, engine registry, compatibility failures, and built-in fallback behavior.
- Added `dander plugins install` and wired generated source-free Dockerfiles to install declared plugins.
- Prepared the isolated contract as `0.4.0rc1`; it is not tagged or published.

## Try It

```bash
uv run dander plugins install --config dander.yaml
uv run dander validate
```

## Checks

- Ruff lint/format, strict mypy, and all 690 tests passed.
- Terraform formatting, backend-disabled initialization, and validation passed for platform and stage zero.
- Dependency audit reported no known vulnerabilities.
- `0.4.0rc1` wheel and sdist passed archive validation and installed outside the checkout.
- Both installed artifacts generated and validated source-free projects with the plugin-install build step.

## Decisions

- Only manifest-declared exact package pins are active; unrelated global packages remain ignored.
- Explicit plugins may replace a built-in engine, while duplicate plugin engines fail.
- Authentication remains in Dander core; the Salesforce implementation has not moved yet.

## Remaining

- Push the focused Dander contract PR and let protected CI repeat Linux checks.
- Obtain explicit approval before tagging or publishing `0.4.0rc1`; generated images cannot install it publicly before then.
- Obtain explicit approval before creating the public Salesforce plugin repository.
- Move Salesforce behavior unchanged only after the core contract is published.
- Add Dander descriptor serving and Druff discovery in a later isolated PR.

## Review First

- `src/dander/plugins/registry.py`
- `src/dander/plugins/contracts.py`
- `src/dander/project/config.py`
