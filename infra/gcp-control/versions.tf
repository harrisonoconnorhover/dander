terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "gcs" {}
}

provider "google" {
  project                     = var.project_id
  region                      = var.region
  impersonate_service_account = var.bootstrap_service_account
}
