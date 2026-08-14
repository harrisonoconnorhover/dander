# Local hosted Control plane

This is the D7 local Docker Compose profile: one same-origin HTTPS endpoint for Druff and the
Dander Control API, backed by a restart-safe local GraphStore volume. It consumes only exact
digest-addressed Dander and Druff images. It never builds from a checkout and never stores an OIDC
client secret or cloud credential.

## Current qualification status

The deterministic projection, preflight, Compose configuration, rollback path, and verifier are
implemented and live-qualified. On 2026-08-14, exact locally loaded Dander RC20 and Druff DRUFF-29
digests passed same-origin HTTPS, synthetic OIDC/PKCE, graph restart persistence, repeat rendering,
stable second-up container identities, digest rollback/restoration, and exact cleanup. The
coordinate-free record is
`docs/evidence/local/2026-08-14/d7-control-plane.json`. This proves the local profile only; it does
not qualify a real identity provider, Kubernetes, or a cloud deployment.

Local Compose has no Terraform backend, saved plan, provider workload identity, or cloud cost
ceiling. Its equivalent drift proof is two identical `docker compose config` renders followed by a
second `up` that preserves the three running container IDs.

## Prepare the non-secret input

Docker Desktop/Engine with Compose v2, Python 3.12 with Dander installed, and OpenSSL are required.
Copy the example into the ignored operator directory and replace all placeholders with exact image
digests and the public OIDC registration values:

```bash
mkdir -p .dander/local-control-plane
chmod 0700 .dander/local-control-plane
cp infra/local/local-control-plane.example.json .dander/local-control-plane-input.json
```

The issuer registration must use:

- API/base URL and allowed origin: `https://localhost:8443`
- SPA redirect: `https://localhost:8443/auth/callback`
- post-logout redirect: `https://localhost:8443/signed-out`
- separate public SPA client ID and Control API audience
- authorization-code flow with PKCE and no client secret

Generate a short-lived localhost certificate. Mode `0444` lets the non-root Caddy process read the
bind-mounted files; both files remain protected by the enclosing mode-`0700` operator directory and
are mounted read-only:

```bash
mkdir -p .dander/local-control-plane/tls
chmod 0700 .dander/local-control-plane/tls
openssl req -x509 -newkey rsa:2048 -nodes -days 7 \
  -config infra/local/localhost-openssl.cnf \
  -keyout .dander/local-control-plane/tls/localhost.key \
  -out .dander/local-control-plane/tls/localhost.crt
chmod 0444 \
  .dander/local-control-plane/tls/localhost.key \
  .dander/local-control-plane/tls/localhost.crt
```

The certificate is intentionally local and self-signed. Trust it only for this disposable profile,
or accept the browser warning for `localhost`; do not install it as a broad system CA.

The renderer applies the same sealed-directory pattern to every generated non-secret file: the
outer directory remains mode `0700`, while files are mode `0444` so UID/GID 65532 can read their
read-only bind mounts on both Linux and Docker Desktop.

## Render and start

```bash
uv run python -m dander.deployment.local_compose render \
  --input .dander/local-control-plane-input.json \
  --output .dander/local-control-plane
uv run python -m dander.deployment.local_compose preflight \
  --input .dander/local-control-plane-input.json \
  --output .dander/local-control-plane
docker compose \
  --project-name dander-local-control-plane \
  --env-file .dander/local-control-plane/active.env \
  --file infra/local/compose.yaml \
  config > .dander/local-control-plane/compose.before.yaml
docker compose \
  --project-name dander-local-control-plane \
  --env-file .dander/local-control-plane/active.env \
  --file infra/local/compose.yaml \
  up --detach
```

Only `127.0.0.1:8443` is published. Control can reach the fixed HTTPS issuer/JWKS URI, but Control
and Druff have no host-published ports. A one-shot root service has only `CHOWN` and `FOWNER`, no
network, and only changes the named GraphStore volume's owner and mode; all long-running services
use UID/GID 65532, read-only root filesystems, dropped capabilities, and bounded tmpfs storage.

Verify the active deployment:

```bash
uv run python -m dander.deployment.local_compose verify \
  --input .dander/local-control-plane-input.json \
  --output .dander/local-control-plane \
  --environment active
```

Then open `https://localhost:8443`, sign in, create a disposable graph, restart only `control`, and
confirm the graph and its revision survive:

```bash
docker compose \
  --project-name dander-local-control-plane \
  --env-file .dander/local-control-plane/active.env \
  --file infra/local/compose.yaml \
  restart control
```

## No-drift, rollback, and cleanup

Run `config` and `up --detach` a second time with the unchanged active environment. Compare the
second rendered YAML byte-for-byte with `compose.before.yaml`, and compare `docker compose ps -q`
before and after the second `up`; the three running service IDs must be unchanged.

Exercise the accepted rollback pair, confirm the same graph persists, then restore active:

```bash
docker compose --project-name dander-local-control-plane \
  --env-file .dander/local-control-plane/rollback.env \
  --file infra/local/compose.yaml up --detach
uv run python -m dander.deployment.local_compose verify \
  --input .dander/local-control-plane-input.json \
  --output .dander/local-control-plane \
  --environment rollback
docker compose --project-name dander-local-control-plane \
  --env-file .dander/local-control-plane/active.env \
  --file infra/local/compose.yaml up --detach
uv run python -m dander.deployment.local_compose verify \
  --input .dander/local-control-plane-input.json \
  --output .dander/local-control-plane \
  --environment active
```

After exporting anything worth keeping, delete only this disposable stack and its GraphStore:

```bash
docker compose --project-name dander-local-control-plane \
  --env-file .dander/local-control-plane/active.env \
  --file infra/local/compose.yaml down --volumes --remove-orphans
```

Delete the generated operator directory as well when the run is complete; it contains the
disposable localhost private key. The accepted immutable image objects may remain in Docker's
content store when the next D7 profile will reuse them, but the disposable registry container and
volume must be removed.
