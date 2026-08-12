locals {
  tags = merge(var.freeform_tags, {
    component  = "oci-stage-zero"
    managed-by = "dander"
  })
}

data "oci_objectstorage_namespace" "current" {
  compartment_id = var.tenancy_id
}

resource "oci_objectstorage_bucket" "terraform_state" {
  compartment_id = var.compartment_id
  namespace      = data.oci_objectstorage_namespace.current.namespace
  name           = var.state_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
  freeform_tags  = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_artifacts_container_repository" "runtime" {
  compartment_id = var.compartment_id
  display_name   = var.repository_name
  is_public      = false
  freeform_tags  = local.tags
}
