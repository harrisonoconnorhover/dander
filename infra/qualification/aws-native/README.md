# Phase 8 AWS-native qualification data plane

This qualification-only Terraform root creates the disposable dependencies consumed by the named
Fargate/Redshift/PostgreSQL/Glue/AWS-Secrets profile. It is not product infrastructure and never
manages `infra/aws-control` or another retained Dander stack.

The root owns one three-AZ VPC, encrypted staging bucket, Redshift Serverless namespace/workgroup,
8-RPU base capacity with a 5 RPU-hour daily deactivation limit, one encrypted `db.t4g.micro`
PostgreSQL 15 state instance, its generated DSN in Secrets Manager, a prefix-scoped Redshift COPY
role, and the exact `dander_analytics_staging.stg_phase8_aws__posts` Glue projection. The namespace
creator also provisions a `dander_runtime` database role with only the DDL and default COPY-role
permissions needed by the qualification writer; the Fargate task maps that role through its
`RedshiftDbRoles` IAM tag. Sensitive values remain only in Secrets Manager and the existing
encrypted remote state.

The root deliberately exposes public subnets and exports `network.assign_public_ip = true` for this
disposable fixture. The Fargate task has no inbound rule and may use only TLS public egress plus the
self-scoped PostgreSQL and Redshift rules. Do not set the fixture task to `assign_public_ip = false`:
there is no NAT gateway or private ECR, CloudWatch Logs, and Secrets Manager endpoint set.

Initialize with a dedicated qualification state key and required exact `candidate_version` and
unique `name` inputs, create and inspect a saved plan, and apply only that plan. The candidate input
owns both the resource tag and staging prefix; an invalid or final-release version is rejected.
Export the non-secret outputs into a temporary version-2 project manifest before
planning `infra/aws`. The runtime updates the Terraform-predeclared Glue table; it does not create
an unowned qualification asset. After the approved manual/replay executions and evidence
collection, destroy the Fargate root first and this data-plane root second. Confirm Redshift, RDS,
S3 objects, the Glue table/database, Secrets Manager secrets, network resources, IAM roles, and
both Terraform states are clean before closing the run.

RC22's historical objective is invalidated and cannot be rerun with this fixture. Bind a new exact
candidate objective and authorization before mutation. This root does not promote a final candidate,
transfer evidence, or authorize public release.
