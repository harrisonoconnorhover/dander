# Morning Handoff

## Finished

- Added strict hosted OIDC access-token verification with bounded, single-flight JWKS refresh.
- Centralized viewer/editor/operator/admin authorization over five server-enforced capabilities.
- Projected server trust, exact CORS, public PKCE client, and bootstrap from one typed input.
- Preserved unauthenticated loopback mode while requiring OIDC configuration for external binds.
- Added the generated `control-bootstrap` contract and completed DANDER-126 documentation.

## Try It

Run `uv run pytest -q tests/control/test_oidc_auth.py tests/control/test_hosted_control.py tests/cli/test_control_oidc_cli.py`.

## Checks

- Full pytest suite, Ruff lint/format, and mypy over 396 sources passed.
- Generated Control contract drift and focused 66-test Control/CLI suite passed.
- Dependency audit reported no known vulnerabilities; wheel and sdist validation passed.
- No external issuer, provider resource, credential, paid action, or public artifact was changed.

## Decisions

- Public SPA client ID and API audience are separate; only authorization code plus PKCE S256 is projected.
- Hosted Control is stateless bearer-only, so cookie/session CSRF controls are intentionally inapplicable.
- Human OIDC claims never become cloud workload identity or provider credentials.

## Remaining

- Complete independent completion review and protected PR CI for DANDER-126.
- Begin Phase D5 Druff remote-client integration only after exact-main CI is green.
- Register a real OIDC client only with separate explicit approval in its later deployment step.

## Review First

- `src/dander/control/auth.py`
- `src/dander/control/http.py`
- `tests/control/test_oidc_auth.py`
