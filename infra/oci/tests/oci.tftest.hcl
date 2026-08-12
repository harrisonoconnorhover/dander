mock_provider "oci" {
  mock_resource "oci_functions_function" {
    override_during = plan
    defaults = {
      id = "ocid1.fnfunc.oc1.iad.ffffffffffffffffffffffffffffffff"
    }
  }

  mock_data "oci_core_services" {
    defaults = {
      services = [{
        cidr_block = "all-iad-services-in-oracle-services-network"
        id         = "ocid1.service.oc1.iad.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        name       = "All IAD Services In Oracle Services Network"
      }]
    }
  }
}

variables {
  tenancy_id               = "ocid1.tenancy.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  compartment_id           = "ocid1.compartment.oc1..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  region                   = "us-ashburn-1"
  object_storage_namespace = "unitnamespace"
  dynamic_group_name       = "dander_phase7_runtime"
}

run "projects_private_keyless_foundation" {
  command = plan

  assert {
    condition = (
      oci_core_subnet.runtime.prohibit_public_ip_on_vnic &&
      oci_core_subnet.runtime.prohibit_internet_ingress &&
      oci_core_nat_gateway.runtime.block_traffic == false &&
      length(oci_core_security_list.runtime.ingress_security_rules) == 0
    )
    error_message = "The runtime subnet must remain private, ingress-free, and outbound-capable."
  }

  assert {
    condition = (
      oci_kms_vault.runtime.vault_type == "DEFAULT" &&
      oci_kms_key.runtime.protection_mode == "SOFTWARE" &&
      !oci_kms_key.runtime.is_auto_rotation_enabled
    )
    error_message = "The runtime Vault must use the bounded-cost default tier and must not request its unsupported automatic key rotation."
  }

  assert {
    condition = (
      oci_identity_dynamic_group.runtime.matching_rule == "ALL {resource.type='computecontainerinstance', resource.compartment.id='ocid1.compartment.oc1..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'}" &&
      length(oci_identity_policy.runtime.statements) == 4
    )
    error_message = "Only Container Instances in the selected compartment may receive runtime OCI permissions."
  }
}

run "projects_idempotent_lifecycle_controller" {
  command = plan

  variables {
    controller_image        = "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime:phase7-controller-unit"
    controller_image_digest = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    execution_projections = {
      jobs = {
        schema                  = "io.dander.execution/v1"
        contract                = "io.dander.runtime/v1"
        pipeline_id             = "jobs"
        profile_id              = "oci_postgresql"
        launcher                = "oci_container_instances"
        image                   = "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        command                 = ["runtime", "execute"]
        configuration_reference = "/app/dander.yaml"
        environment             = {}
        secret_bindings         = {}
        workload_identity       = "oci-resource-principal://dynamic-group/dander_phase7_runtime"
        resources = {
          cpu_millis            = 1000
          memory_mib            = 2048
          ephemeral_storage_mib = null
          deadline_seconds      = 900
          runtime_retry_count   = 0
          launcher_retry_count  = 1
        }
        schedule = {
          task_count          = 1
          maximum_parallelism = 1
          expression          = "0 * * * *"
          time_zone           = "UTC"
          paused              = true
        }
        network = {
          placement = "ocid1.subnet.oc1.iad.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
          extensions = {
            oci_assign_public_ip    = "false"
            oci_availability_domain = "unit:US-ASHBURN-AD-1"
          }
        }
        labels = {}
        observability = {
          log_destination  = "oci_logging"
          metric_namespace = "oci_computecontainerinstance"
          alert_target     = "oci_notifications"
          retention_days   = 30
        }
        extensions = {
          oci_compartment_id            = "ocid1.compartment.oc1..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
          oci_graceful_shutdown_seconds = "120"
          oci_registry_endpoint         = "ocir.us-ashburn-1.oci.oraclecloud.com"
          oci_restart_policy            = "NEVER"
          oci_shape                     = "CI.Standard.E4.Flex"
          oci_tenancy_id                = "ocid1.tenancy.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
          oci_vault_id                  = "ocid1.vault.oc1.iad.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        }
      }
    }
  }

  assert {
    condition = (
      oci_objectstorage_bucket.run_records.access_type == "NoPublicAccess" &&
      oci_objectstorage_bucket.run_records.versioning == "Enabled" &&
      oci_functions_application.controller[0].shape == "GENERIC_X86" &&
      oci_functions_function.pipeline["jobs"].image_digest == "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" &&
      oci_functions_function.pipeline["jobs"].detached_mode_timeout_in_seconds == 3600 &&
      oci_resource_scheduler_schedule.pipeline["jobs"].state == "INACTIVE"
    )
    error_message = "The lifecycle controller must use private versioned records, an immutable Function, and paused-aware scheduling."
  }

  assert {
    condition = (
      oci_identity_dynamic_group.controller[0].matching_rule == format("ANY {resource.id='%s'}", oci_functions_function.pipeline["jobs"].id) &&
      length(oci_identity_policy.controller[0].statements) == 3 &&
      oci_events_rule.container_lifecycle["jobs"].is_enabled &&
      strcontains(oci_events_rule.container_lifecycle["jobs"].condition, "dander-pipeline") &&
      oci_logging_log.function_invocations[0].configuration[0].source[0].category == "invoke" &&
      oci_monitoring_alarm.function_errors[0].namespace == "oci_faas"
    )
    error_message = "The controller must remain keyless, event-assisted, logged, and alarmed."
  }
}
