# Morning Handoff

## Finished

- Reproduced the RC31 boundary: integrated-IAM validation timed out while explicit credentials validated.
- Cleaned the disposable launcher, data plane, staging objects, state versions, and lock metadata.
- Changed only Serverless connections to use Dander-acquired temporary credentials with integrated IAM disabled.
- Preserved provisioned Redshift, TLS verification, protocol selection, and the 300-second timeout.
- Recorded sanitized diagnostic evidence without credential or endpoint payloads.

## Try It

Run `uv run pytest -q tests/providers/test_redshift_warehouse_runtime.py`.

## Checks

- Full pytest passes: 1,936 passed and 35 skipped.
- Ruff lint/format, strict typing, Control contract drift, and release metadata checks pass.

## Decisions

- Request 900-second Serverless credentials for each new connection; do not cache secrets in Dander.
- Reject incomplete credential responses before invoking the connector.

## Remaining

- Protect and merge this correction, then require exact-main CI.
- Publish one immutable replacement candidate from that protected commit.
- Protect corrective objectives and rerun only Redshift cells blocked by this shared boundary.
- Reconcile the delayed provider cost without rerunning for billing data alone.

## Review First

- `src/dander/providers/redshift/runtime.py`
- `tests/providers/test_redshift_warehouse_runtime.py`
- `docs/evidence/phase8/2026-08-23/aws-native-rc31-redshift-connection-reproduction.json`
