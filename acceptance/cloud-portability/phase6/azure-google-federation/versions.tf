terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.9"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.72"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "azuread" {
  tenant_id = var.tenant_id
}

provider "azurerm" {
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"

  features {}
}

provider "google" {
  project = var.gcp_project_id
}
