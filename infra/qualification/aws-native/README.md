# Phase 8 AWS-native qualification data plane

This qualification-only Terraform root creates the disposable dependencies consumed by the named
Fargate/Redshift/PostgreSQL/Glue/AWS-Secrets profile. It is not product infrastructure and never
manages `infra/aws-control` or another retained Dander stack.

The root owns one three-AZ VPC, encrypted staging bucket, Redshift Serverless namespace/workgroup,
8-RPU base capacity with a 5 RPU-hour daily deactivation limit, one encrypted `db.t4g.micro`
PostgreSQL 15 state instance, its generated DSN in Secrets Manager, and a prefix-scoped Redshift
COPY role. Sensitive values remain only in Secrets Manager and the existing encrypted remote state.

Initialize with a dedicated qualification state key, create and inspect a saved plan, and apply
only that plan. Export the non-secret outputs into a temporary version-2 project manifest before
planning `infra/aws`. After the approved manual/replay executions and evidence collection, destroy
the Fargate root first and this data-plane root second. Confirm Redshift, RDS, S3 objects, Glue
databases, Secrets Manager secrets, network resources, IAM roles, and both Terraform states are
clean before closing the run.

This fixture is bound to the committed RC22 AWS-native objectives and USD 3 allocation. It does not
promote RC22 as the final candidate, transfer evidence to a successor, or authorize public release.
