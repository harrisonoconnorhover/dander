mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b", "us-east-1c"]
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/dander-p8q-rc22-redshift-copy"
    }
  }

  mock_resource "aws_redshiftserverless_workgroup" {
    defaults = {
      arn = "arn:aws:redshift-serverless:us-east-1:123456789012:workgroup/00000000-0000-0000-0000-000000000000"
      endpoint = [{
        address      = "dander-p8q-rc22.123456789012.us-east-1.redshift-serverless.amazonaws.com"
        port         = 5439
        vpc_endpoint = []
      }]
    }
  }
}

mock_provider "random" {}

variables {
  aws_account_id = "123456789012"
  region         = "us-east-1"
  name           = "dander-p8q-rc22"
}

run "bounded_disposable_data_plane" {
  command = apply

  assert {
    condition = (
      aws_redshiftserverless_workgroup.profile.base_capacity == 8 &&
      aws_redshiftserverless_usage_limit.compute.amount == 5 &&
      aws_redshiftserverless_usage_limit.compute.period == "daily" &&
      aws_redshiftserverless_usage_limit.compute.breach_action == "deactivate"
    )
    error_message = "Redshift Serverless must retain the approved 8-RPU and 5-RPU-hour deactivation boundary."
  }

  assert {
    condition = (
      aws_db_instance.postgresql.instance_class == "db.t4g.micro" &&
      aws_db_instance.postgresql.allocated_storage == 20 &&
      aws_db_instance.postgresql.storage_encrypted &&
      !aws_db_instance.postgresql.publicly_accessible &&
      aws_db_instance.postgresql.skip_final_snapshot
    )
    error_message = "PostgreSQL state must remain small, encrypted, private, and disposable."
  }

  assert {
    condition = (
      length(aws_subnet.profile) == 3 &&
      alltrue([for subnet in aws_subnet.profile : subnet.map_public_ip_on_launch]) &&
      contains(
        [for route in aws_route_table.profile.route : route.gateway_id],
        aws_internet_gateway.profile.id
      ) &&
      output.network.assign_public_ip &&
      aws_vpc_endpoint.s3.vpc_endpoint_type == "Gateway" &&
      contains(aws_vpc_endpoint.s3.route_table_ids, aws_route_table.profile.id) &&
      aws_s3_bucket.staging.force_destroy &&
      aws_s3_bucket_public_access_block.staging.restrict_public_buckets &&
      aws_secretsmanager_secret.postgresql_dsn.recovery_window_in_days == 0
    )
    error_message = "The qualification network must expose the bounded task egress path while its data plane remains exactly owned and destroyable."
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.postgresql.referenced_security_group_id == aws_security_group.profile.id &&
      aws_vpc_security_group_ingress_rule.redshift.referenced_security_group_id == aws_security_group.profile.id &&
      aws_vpc_security_group_egress_rule.postgresql.referenced_security_group_id == aws_security_group.profile.id &&
      aws_vpc_security_group_egress_rule.postgresql.from_port == 5432 &&
      aws_vpc_security_group_egress_rule.postgresql.to_port == 5432 &&
      aws_vpc_security_group_egress_rule.redshift.referenced_security_group_id == aws_security_group.profile.id &&
      aws_vpc_security_group_egress_rule.redshift.from_port == 5439 &&
      aws_vpc_security_group_egress_rule.redshift.to_port == 5439 &&
      aws_vpc_security_group_egress_rule.internet.ip_protocol == "tcp" &&
      aws_vpc_security_group_egress_rule.internet.from_port == 443 &&
      aws_vpc_security_group_egress_rule.internet.to_port == 443
    )
    error_message = "Database traffic must stay self-scoped and public egress must stay limited to TLS."
  }

  assert {
    condition = (
      toset(flatten([
        for rule in aws_s3_bucket_server_side_encryption_configuration.staging.rule : [
          for encryption in rule.apply_server_side_encryption_by_default : encryption.sse_algorithm
        ]
      ])) == toset(["AES256"]) &&
      toset(flatten([
        for rule in aws_s3_bucket_lifecycle_configuration.staging.rule : [
          for expiration in rule.expiration : expiration.days
        ]
      ])) == toset([1])
    )
    error_message = "Disposable staging objects must remain encrypted and expire after one day."
  }

  assert {
    condition = (
      output.redshift.database_role == "dander_runtime" &&
      aws_redshiftdata_statement.runtime_role.sql == "CREATE ROLE dander_runtime" &&
      aws_redshiftdata_statement.runtime_ddl.sql == "GRANT CREATE SCHEMA, CREATE TABLE, ALTER TABLE, DROP TABLE TO ROLE dander_runtime" &&
      aws_redshiftdata_statement.runtime_copy.sql == "GRANT ASSUMEROLE ON default TO ROLE dander_runtime FOR COPY"
    )
    error_message = "The disposable namespace must provision the exact database role required by the Fargate runtime."
  }
}

run "rejects_unauthorized_authenticated_account" {
  command = plan

  variables {
    aws_account_id = "999999999999"
  }

  expect_failures = [aws_vpc.profile]
}
