# Redshift Serverless live qualification

On 2026-08-10, Dander's experimental Redshift warehouse adapter passed its bounded live
qualification in a disposable AWS account. This records runtime evidence; it does not promote
Redshift to a supported profile.

## Boundary

- Terraform created exactly ten disposable resources: Redshift Serverless namespace and
  workgroup, staging bucket, COPY role and policy, network resources, and their required bindings.
- A six RPU-hour operator guard capped Serverless compute below the approved $3 ceiling. AWS
  reported 4,080 charged RPU-seconds (1.133333 RPU-hours), approximately $0.425 of compute at the
  reviewed $0.375 per RPU-hour rate.
- The qualification mutated only its random database schema and S3 staging prefix.

## Result

The sanitized `io.dander.benchmark.redshift/v1` report recorded `passed` in 238.579339 seconds.
Direct insertion wrote two rows in four operations; Parquet `COPY` wrote four rows in 30
operations. SCD1, SCD2, snapshot, incremental, and replace all passed. Explicit `SUPER`, table and
incremental models, and a provider-neutral graph returned their expected rows. Replay remained
duplicate-free, the cursor remained monotonic, a stale publication was rejected, and two
concurrent claim attempts were exercised.

The run finished with zero staging tables and zero staging objects and verified removal of its
random schema and S3 prefix. Terraform then destroyed all ten resources. Postflight checks found
zero state resources, workgroups, namespaces, matching security groups, buckets, and IAM roles.

## Findings

Live execution exposed Redshift system-view coordinate names and an incorrect benchmark relation
name. The runtime now uses `table_catalog`/`table_schema`, and the qualification's physical raw
relation and telemetry lookup match Redshift's actual contracts, with regression coverage.
Support remains experimental; views, provider-managed reusable infrastructure, and support
promotion remain outside this proof.
