output "object_storage_namespace" {
  description = "Object Storage namespace required by the native OCI Terraform backend."
  value       = data.oci_objectstorage_namespace.current.namespace
}

output "state_bucket_name" {
  description = "Private, versioned bucket holding native OCI Terraform state."
  value       = oci_objectstorage_bucket.terraform_state.name
}

output "repository_id" {
  description = "Immutable private OCIR repository OCID."
  value       = oci_artifacts_container_repository.runtime.id
}

output "repository_name" {
  description = "OCIR repository path receiving copied release artifacts."
  value       = oci_artifacts_container_repository.runtime.display_name
}
