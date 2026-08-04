# Morning Handoff

## Finished

- Published and source-free verified Dander `0.4.0rc1` from `a6a8b35e1cfa566d200320fb334f6b8d0b1e15dd`.
- Created the public `dander-connector-salesforce` repository and moved the Bulk API adapter into it.
- Added presentation-safe `GET /v1/connectors` discovery for installed manifest plugins.
- Kept the built-in Salesforce adapter as a deprecated 0.4 fallback.
- Prepared Dander `0.4.0rc2` for the combined plugin/discovery acceptance path.

## Try It

```bash
uv run dander graph serve --file graphs/greenhouse_jobs.yaml --config dander.yaml
curl -H 'Origin: http://localhost:3000' http://127.0.0.1:8765/v1/connectors
```

## Checks

- `0.4.0rc1` protected publication succeeded and the public package generated a valid source-free project.
- Ruff lint/format, strict mypy, and all 691 tests passed.
- Terraform formatting, backend-disabled initialization, and validation passed for platform and stage zero.
- Dependency audit reported no known vulnerabilities.
- `0.4.0rc2` wheel/sdist validation and the local Linux container build passed; the container reported `dander 0.4.0rc2`.
- Protected PR checks passed for Python, Terraform, distribution, container, and secret validation.

## Decisions

- Only exact manifest plugin pins are active; unrelated installed packages remain ignored.
- Connector discovery exposes presentation metadata only; auth and secrets remain in Dander core.
- The live proof must use `0.4.0rc2` plus a published Salesforce plugin candidate.

## Remaining

- Obtain explicit approval before publishing Dander `0.4.0rc2` or the plugin candidate.
- Prepare a fresh disposable GCP project and reviewed Terraform plan.
- Run the isolated source-free Salesforce/Druff proof only after the publication and apply approvals.

## Review First

- `src/dander/pipeline/graph_service.py`
- `src/dander/cli/main.py`
- `tests/pipeline/test_graph_service.py`
