# Dander AWS stage zero

This Terraform root creates only the AWS prerequisites that the Fargate platform root cannot own:
an encrypted/versioned S3 state bucket, DynamoDB lock table, immutable ECR repository, and a
dedicated deployment role trusted by one exact operator principal.

The short-lived deployment role also carries an action-bounded D7 policy for disposable hosted
Control resources. S3 bucket/object access is limited to `${name}-d7-*`; ECS, load-balancer, and
security-group mutations are limited by D7 names or tags; CloudFront creation and lifecycle use
only the exact distribution and policy actions required by that profile. The role can remove
noncurrent retained-state versions only below the fixed `dander/d7/control-plane/` prefix and can
remove versions from disposable D7 buckets, so cleanup does not leave hidden generations or gain
destructive access to unrelated state history. The D7 application root must use that exact backend
prefix and must not manage this role, create a custom domain, or depend on wildcard
provider-administration actions.

The D7 policy also grants only the read calls that the locked AWS provider uses to resolve the
selected VPC and CloudFront prefix list and to refresh the profile's listener, graph bucket, and
tagged log groups. Bucket and log-tag reads remain scoped to disposable D7 resource names; the
provider does not receive wildcard service-read authority.

Security-group creation follows AWS's separate authorization dimensions: the new group must carry
the D7 management tags, creation-time tagging is limited to `CreateSecurityGroup`, and the role may
use account-local VPCs only as the dependent resource for that tagged create. Later mutation and
deletion remain limited to security groups that carry the D7 management tags.

The public Dander lifecycle copies this root into a private operator-artifact directory and uses a
local backend for the first reviewed plan. After that exact plan is applied, Dander migrates the
state into the newly created S3 backend and pins the backend client to the root's customer-managed
KMS alias so Terraform cannot override the bucket default with SSE-S3. The local state remains
available as recovery evidence if migration fails. Do not run this root directly from the
repository checkout.

Stage zero is the only AWS operation that may use an account administrator. Later platform plans,
image promotion, and applies use the returned deployment role through a short-lived AWS profile.
