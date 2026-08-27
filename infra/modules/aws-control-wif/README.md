# AWS Control workload identity

This module lets the one always-on AWS ECS Control task operate existing GCP Cloud Run Jobs
without a stored Google key. It admits only the exact supplied AWS task role, exchanges that role's
renewable ECS credentials through Google Workload Identity Federation, and impersonates one GCP
Control service account.

The GCP account can update/run/cancel Cloud Run Jobs, read their logs, and act as only the runtime
service accounts supplied by the existing scheduled-job module. Pipeline workers continue to use
their existing per-pipeline service accounts and BigQuery permissions.
