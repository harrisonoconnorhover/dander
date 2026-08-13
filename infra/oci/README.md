# OCI Container Instances foundation

This root creates the provider-owned foundation for Dander's named OCI profile:

- a private VCN/subnet with no public IPs or inbound rules;
- NAT egress for PostgreSQL plus an OCI Services gateway for OCIR, Vault, and control APIs;
- a default OCI Vault and software-protected key with manual key-version rotation;
- a compartment-scoped Container Instance dynamic group and least-privilege runtime policy; and
- a private versioned run-record bucket, per-pipeline OCI Function, Resource Scheduler schedule,
  lifecycle event rule, invocation log, error alarm, and Notifications topic.

It deliberately does **not** create long-lived Container Instances. Every task attempt is a new
run-scoped instance created by the Phase 7 launch Function and stopped by the reconciler. It also
does not put secret values in Terraform state; seed named OCI Vault secrets after the foundation
apply and before enabling schedules.

Use Terraform's native `oci` backend with the private stage-zero bucket and a short-lived
`SecurityToken` profile. Review a saved plan before apply. The output subnet and Vault OCIDs are
then copied into the typed `oci_container_instances` launcher configuration.

## Plan sequence

1. Run `dander init-oci-admin-plan`, review the saved stage-zero plan, and apply it only after
   explicit cost approval with `dander init-oci-admin-apply`.
2. Promote the accepted source-free runtime index without rebuilding it:

   ```console
   dander image-promote-oci \
     --source-image SOURCE@sha256:DIGEST \
     --compartment-id ocid1.compartment... \
     --registry-namespace NAMESPACE \
     --repository dander/runtime \
     --oci-profile DANDER
   ```

   The command verifies the accepted local artifact record and source platform map, requires the
   exact private immutable repository, requests a repository-scoped access token from the expiring
   OCI `SecurityToken` session, and uses only a temporary Docker configuration. It rejects any
   index or platform-digest rewrite and records the immutable result locally.
3. Run `dander init-oci-plan` and apply the reviewed foundation plan with `dander init-oci-apply`.
4. Seed only the manifest-declared secret names in Vault. Secret values never enter Terraform.
5. Build and push the [controller image](controller/README.md) from a clean protected-main wheel.
6. Run `dander init-oci-launcher-plan` with the exact runtime digest, controller tag and controller
   digest. Review and apply that saved plan through the same `init-oci-apply` boundary.
7. Use `dander verify-oci-deployment` with all four controller inputs to require exact no drift.

The Function owns maximum parallelism one, whole-task retry only for runtime exit code 75, a
3,300-second maximum runtime deadline, stop/delete cleanup, bounded logs, and immutable history.
Resource Scheduler is UTC-only and does not admit a recurrence interval shorter than one hour.
`dander oci run`, `cancel`, and `replay` require confirmation; `status` and `logs` are read-only.
All operator API calls use an expiring `SecurityToken` profile, while the Function and Container
Instances use resource principals. Runtime promotion derives a short-lived, repository-scoped
registry token from that session and never changes the operator's persistent Docker configuration.
Static OCI API keys, user auth tokens, registry passwords, and static cloud keys are not fallbacks.
Oracle starts scheduled Functions through the `functions-family` resource action, so the
single-schedule dynamic group receives `manage functions-family` only in the runtime compartment.
It cannot manage Functions in other compartments.

OCI automatic master-key rotation is available only on the separately billed virtual private
Vault tier. The bounded-cost default Vault therefore uses a software key with manual key-version
rotation. This provider limitation does not weaken the Phase 7 application-secret proof: changing
the `CURRENT` secret version must still be observed by a later run without rebuilding its image.
