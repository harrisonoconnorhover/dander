terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # The qualification DSN is sensitive, so even this disposable fixture uses the existing
  # encrypted S3 backend. Supply bucket, key, region, and locking through `terraform init`.
  backend "s3" {}
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = merge(var.tags, {
      managed-by = "dander"
      purpose    = "phase8-qualification"
    })
  }
}
