# Security Policy

Dander is alpha software. Use a dedicated GCP project, least-privilege credentials, immutable
container digests, and reviewed Terraform plans. Never put provider tokens, Terraform state, raw
source rows, or recovery codes in an issue or pull request.

## Supported versions

Only the newest published patch of the current `0.x` minor receives security and correctness fixes.
While the next minor is in public candidate acceptance, only its newest candidate also receives
candidate fixes. After a final, candidate, or patch is superseded, operators should upgrade.

| Version | Supported |
|---|---|
| Newest patch of the current published minor | Yes |
| Newest public candidate for the next minor | Acceptance fixes only |
| Superseded patch, candidate, or older minor | No |

Publishing a new final minor ends support for the preceding minor. This policy does not promise a
response or fix deadline.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/harrisonoconnorhover/dander/security/advisories/new).
Include the affected Dander version, impact, reproduction using invented data, and any mitigation.
Do not open a public issue until disclosure has been coordinated.

Ordinary bugs that contain no sensitive security detail belong in the public
[issue tracker](https://github.com/harrisonoconnorhover/dander/issues).
