# AWS hosted Control profile

This separate partial-backend Terraform root projects one bounded D7 deployment: one public
CloudFront HTTPS origin, a CloudFront-only public ALB, distinct single-instance Fargate services
for Dander Control and Druff, and a private versioned S3 GraphStore bucket. It reuses the D6
service and hosted-OIDC contracts. Only the Control task role receives GraphStore permissions.

The provider assigns the CloudFront domain, so the profile deliberately uses two reviewed applies
in the same state. The first `foundation_only` apply creates the disposable bucket and ingress
foundation. Its output closes the exact browser/API origin in the full input. The second saved plan
adds the two services. It must reuse the foundation's exact distribution id and domain.

Startup JSON is public or credential-free configuration, not a secret. The projection embeds its
base64 form in each sensitive task definition. A fixed root init process from the same immutable
Dander image writes mode `0444` files to an ephemeral volume, then exits successfully before the
nonroot application starts with that volume read-only. This avoids another config store, identity,
or credential path. Both long-running containers use read-only roots, dropped capabilities, and a
writable ephemeral `/tmp`.

CloudFront disables caching for `/v1/*`, `/healthz`, and `/readyz`; forwards viewer headers and all
query strings but no cookies; and leaves the static minimum TTL at zero so Caddy's `no-store` and
immutable-asset headers remain authoritative. CloudFront and ALB access logging stay disabled so
OIDC callback query values cannot be persisted by the front proxy. Dander and Caddy also retain
their reviewed no-access-log configurations.

Generate the foundation projection without provider access:

```bash
python -m dander.deployment.aws_control_plane render \
  --input /secure/local/aws-control-foundation.json \
  --output /secure/local/aws-control-render
python -m dander.deployment.aws_control_plane preflight \
  --input /secure/local/aws-control-foundation.json \
  --output /secure/local/aws-control-render \
  --terraform-root infra/aws-control
```

Initialize the backend only below `dander/d7/control-plane/`, using the retained state bucket,
DynamoDB lock table, SSE-KMS settings, and reviewed deployment-role assumption. Save and review
every plan outside the checkout. After the foundation apply, copy the accepted Dander and Druff
manifests into the retained ECR repository without rebuilding, add the returned CloudFront
identity plus exact image digests and hosted OIDC input, then render/review the complete active
plan. `rollback.tfvars.json` changes only the two application digests.

The live verifier is read-only. Qualification must prove HTTPS OIDC, browser graph persistence,
S3 conformance, restart, digest rollback/restore, a stable no-change plan, exact removal of every
graph version and D7 state generation, and retained AWS/GCP no-drift. This remains experimental:
it makes no HA, autoscaling, custom-domain, WAF, or AWS support-promotion claim.
