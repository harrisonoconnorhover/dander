output "runtime_subnet_id" {
  description = "Private subnet OCID selected by the OCI launcher."
  value       = oci_core_subnet.runtime.id
}

output "vault_id" {
  description = "Vault OCID selected by the OCI Vault provider."
  value       = oci_kms_vault.runtime.id
}

output "vault_key_id" {
  description = "Auto-rotating software-protected key for runtime secrets."
  value       = oci_kms_key.runtime.id
}

output "dynamic_group_name" {
  description = "Dynamic group selected by the OCI launcher resource principal."
  value       = oci_identity_dynamic_group.runtime.name
}

output "log_group_id" {
  description = "OCI log group for Container Instance and controller logs."
  value       = oci_logging_log_group.runtime.id
}

output "notification_topic_id" {
  description = "OCI Notifications topic for execution and reconciliation alerts."
  value       = oci_ons_notification_topic.runtime.id
}

output "run_records_bucket" {
  description = "Private versioned Object Storage bucket for non-secret projections and runs."
  value       = oci_objectstorage_bucket.run_records.name
}

output "controller" {
  description = "Lifecycle Function and Resource Scheduler identifiers by pipeline."
  value = {
    for id, function in oci_functions_function.pipeline : id => {
      function_id    = function.id
      schedule_id    = oci_resource_scheduler_schedule.pipeline[id].id
      schedule_state = oci_resource_scheduler_schedule.pipeline[id].state
    }
  }
}

output "network" {
  description = "Private runtime VCN resources used by run-scoped Container Instances."
  value = {
    vcn_id          = oci_core_vcn.runtime.id
    subnet_id       = oci_core_subnet.runtime.id
    nat_gateway_id  = oci_core_nat_gateway.runtime.id
    service_gateway = oci_core_service_gateway.runtime.id
  }
}
