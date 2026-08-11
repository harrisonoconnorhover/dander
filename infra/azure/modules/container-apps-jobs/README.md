# Container Apps Jobs module

This module accepts only validated Azure execution projections. It creates one Container Apps Job
per pipeline with the exact immutable ACR digest, user-assigned identity, CPU/memory pair, deadline,
launcher retry count, environment, Key Vault references, trigger, labels, and observability target.

Paused projections use a manual trigger. Active projections use the already validated UTC cron.
The selected managed identity pulls from ACR and reads only the selected Key Vault; Terraform never
receives a secret value. The vault defaults to deny and allows the Azure service path plus one
reviewed operator IP for secret administration. A projection that names a different registry,
identity, environment, client id, vault, or secret provider fails its Terraform check.

An optional existing infrastructure subnet creates an internal Container Apps environment. Failed
execution alerts use the Azure `Microsoft.App/jobs` `Executions` metric filtered to `Failed` and
send to the projection's existing Action Group id.
