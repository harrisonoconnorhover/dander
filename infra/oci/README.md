# OCI Container Instances foundation

This root creates the provider-owned foundation for Dander's named OCI profile:

- a private VCN/subnet with no public IPs or inbound rules;
- NAT egress for PostgreSQL plus an OCI Services gateway for OCIR, Vault, and control APIs;
- a default OCI Vault and auto-rotating software-protected key;
- a compartment-scoped Container Instance dynamic group and least-privilege runtime policy; and
- a log group and Notifications topic for the lifecycle controller.

It deliberately does **not** create long-lived Container Instances. Every task attempt is a new
run-scoped instance created by the Phase 7 launch Function and stopped by the reconciler. It also
does not put secret values in Terraform state; seed named OCI Vault secrets after the foundation
apply and before enabling schedules.

Use Terraform's native `oci` backend with the private stage-zero bucket and a short-lived
`SecurityToken` profile. Review a saved plan before apply. The output subnet and Vault OCIDs are
then copied into the typed `oci_container_instances` launcher configuration.
