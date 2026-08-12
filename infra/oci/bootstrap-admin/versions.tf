terraform {
  required_version = ">= 1.12, < 2.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 8.26"
    }
  }
}

provider "oci" {
  region              = var.region
  tenancy_ocid        = var.tenancy_id
  auth                = "SecurityToken"
  config_file_profile = var.config_file_profile
}
