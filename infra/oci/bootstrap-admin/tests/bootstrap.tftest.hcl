mock_provider "oci" {
  mock_data "oci_objectstorage_namespace" {
    defaults = {
      namespace = "unitnamespace"
    }
  }
}

variables {
  tenancy_id        = "ocid1.tenancy.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  compartment_id    = "ocid1.compartment.oc1..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  region            = "us-ashburn-1"
  state_bucket_name = "dander-phase7-state"
  repository_name   = "dander/runtime"
}

run "private_digest_addressed_stage_zero" {
  command = plan

  assert {
    condition = (
      oci_objectstorage_bucket.terraform_state.access_type == "NoPublicAccess" &&
      oci_objectstorage_bucket.terraform_state.versioning == "Enabled" &&
      oci_objectstorage_bucket.terraform_state.storage_tier == "Standard"
    )
    error_message = "OCI Terraform state must remain private, versioned, and online."
  }

  assert {
    condition = (
      !oci_artifacts_container_repository.runtime.is_public &&
      oci_artifacts_container_repository.runtime.display_name == "dander/runtime"
    )
    error_message = "OCIR must remain private and use the selected digest-addressed repository."
  }
}
