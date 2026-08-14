terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Supply bucket, key, region, encryption, DynamoDB locking, and the reviewed
  # deployment-role assumption through `terraform init -backend-config`.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  assume_role {
    role_arn     = var.deployment_role_arn
    session_name = "dander-d7-control-plane"
  }

  default_tags {
    tags = {
      managed-by = "dander"
      phase      = "d7"
      profile    = "aws-control"
    }
  }
}
