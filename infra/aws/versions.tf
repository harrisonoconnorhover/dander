terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Supply bucket, key, region, encryption, and native lockfile settings through
  # `terraform init -backend-config`. Provider credentials never belong in this file.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(var.tags, {
      managed-by = "dander"
    })
  }
}
