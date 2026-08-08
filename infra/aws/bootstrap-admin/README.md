# Dander AWS stage zero

This Terraform root creates only the AWS prerequisites that the Fargate platform root cannot own:
an encrypted/versioned S3 state bucket, DynamoDB lock table, immutable ECR repository, and a
dedicated deployment role trusted by one exact operator principal.

The public Dander lifecycle copies this root into a private operator-artifact directory and uses a
local backend for the first reviewed plan. After that exact plan is applied, Dander migrates the
state into the newly created S3 backend. The local state remains available as recovery evidence if
migration fails. Do not run this root directly from the repository checkout.

Stage zero is the only AWS operation that may use an account administrator. Later platform plans,
image promotion, and applies use the returned deployment role through a short-lived AWS profile.
