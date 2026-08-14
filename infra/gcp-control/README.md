# GCP hosted Control profile

This separate Terraform root projects one bounded D7 deployment: public Cloud Run services for
Dander Control and Druff, a disposable GCS GraphStore bucket, distinct keyless service accounts,
and numeric startup-config versions mounted from Secret Manager. Application OIDC protects Control
API routes; Google IAM is not asked to interpret the external browser token.

The root uses a partial GCS backend. Live qualification initializes it against an attempt-specific
prefix in the retained hardened state bucket, saves every plan outside the checkout, and applies
only the reviewed saved plan. It never shares state or resource ownership with `infra/`.

The graph bucket is versioned for fencing but explicitly disables soft-delete retention because the
qualification profile is disposable and cleanup must leave no recoverable graph data. The retained
Terraform-state bucket keeps its existing recovery policy unchanged.

Generate `active.tfvars.json`, `rollback.tfvars.json`, and the matching deployment manifest through
`python -m dander.deployment.gcp_control_plane`. The rendered files contain only public OIDC
coordinates, credential-free storage locators, immutable images, and startup configuration; they
must still remain outside commits and use mode `0444` under a mode `0700` directory. Credentials,
tokens, Terraform state, saved plans, and graph rows never belong in a rendered file or evidence.

Start from `gcp-control-plane.example.json`, replace every example coordinate and image digest,
then render and validate without contacting a backend:

```bash
python -m dander.deployment.gcp_control_plane render \
  --input /secure/local/gcp-control-plane.json \
  --output /secure/local/gcp-control-render
python -m dander.deployment.gcp_control_plane preflight \
  --input /secure/local/gcp-control-plane.json \
  --output /secure/local/gcp-control-render \
  --terraform-root infra/gcp-control
```

Live use must initialize the partial backend with the rendered attempt-specific bucket and prefix,
review a saved plan outside the checkout, and explicitly apply that exact plan. The verifier is
read-only; run it once for `active` and again after selecting `rollback`. Destroy every resource in
this root and verify that neither live object versions nor a soft-deleted graph bucket remain.
