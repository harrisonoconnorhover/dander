locals {
  tags = merge(var.freeform_tags, {
    component  = "oci-container-instances"
    managed-by = "dander"
  })
  controller_enabled = var.controller_image != null && var.controller_image_digest != null && length(var.execution_projections) > 0
  resource_schedule_ids = [
    for schedule in oci_resource_scheduler_schedule.pipeline : schedule.id
  ]
}

check "controller_inputs_are_atomic" {
  assert {
    condition = (
      (var.controller_image == null && var.controller_image_digest == null && length(var.execution_projections) == 0) ||
      local.controller_enabled
    )
    error_message = "controller_image, controller_image_digest, and execution_projections must be supplied together."
  }
}

data "oci_core_services" "all_services" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

check "one_regional_service_gateway_target" {
  assert {
    condition     = length(data.oci_core_services.all_services.services) == 1
    error_message = "OCI must expose exactly one regional All Services Network target."
  }
}

resource "oci_core_vcn" "runtime" {
  compartment_id = var.compartment_id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.name}-runtime"
  dns_label      = "dander"
  freeform_tags  = local.tags
}

resource "oci_core_nat_gateway" "runtime" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.runtime.id
  display_name   = "${var.name}-runtime-egress"
  block_traffic  = false
  freeform_tags  = local.tags
}

resource "oci_core_service_gateway" "runtime" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.runtime.id
  display_name   = "${var.name}-oci-services"
  freeform_tags  = local.tags

  services {
    service_id = one(data.oci_core_services.all_services.services).id
  }
}

resource "oci_core_route_table" "runtime" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.runtime.id
  display_name   = "${var.name}-runtime-routes"
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.runtime.id
  }

  route_rules {
    destination       = one(data.oci_core_services.all_services.services).cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.runtime.id
  }
}

resource "oci_core_security_list" "runtime" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.runtime.id
  display_name   = "${var.name}-runtime-egress-only"
  freeform_tags  = local.tags

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
    stateless   = false
  }
}

resource "oci_core_subnet" "runtime" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.runtime.id
  cidr_block                 = var.runtime_subnet_cidr
  display_name               = "${var.name}-runtime"
  dns_label                  = "runtime"
  prohibit_internet_ingress  = true
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.runtime.id
  security_list_ids          = [oci_core_security_list.runtime.id]
  freeform_tags              = local.tags
}

resource "oci_kms_vault" "runtime" {
  compartment_id = var.compartment_id
  display_name   = "${var.name}-runtime"
  vault_type     = "DEFAULT"
  freeform_tags  = local.tags
}

resource "oci_kms_key" "runtime" {
  compartment_id           = var.compartment_id
  display_name             = "${var.name}-runtime"
  management_endpoint      = oci_kms_vault.runtime.management_endpoint
  protection_mode          = "SOFTWARE"
  is_auto_rotation_enabled = true
  freeform_tags            = local.tags

  auto_key_rotation_details {
    rotation_interval_in_days = 365
  }

  key_shape {
    algorithm = "AES"
    length    = 32
  }
}

resource "oci_identity_dynamic_group" "runtime" {
  compartment_id = var.tenancy_id
  name           = var.dynamic_group_name
  description    = "Dander run-scoped OCI Container Instance resource principals"
  matching_rule  = "ALL {resource.type='computecontainerinstance', resource.compartment.id='${var.compartment_id}'}"
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "runtime" {
  compartment_id = var.tenancy_id
  name           = "${var.name}-container-runtime"
  description    = "Least-privilege OCI service access for Dander Container Instances"
  freeform_tags  = local.tags
  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.runtime.name} to read repos in compartment id ${var.compartment_id}",
    "Allow dynamic-group ${oci_identity_dynamic_group.runtime.name} to read secret-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group ${oci_identity_dynamic_group.runtime.name} to use keys in compartment id ${var.compartment_id}",
    "Allow dynamic-group ${oci_identity_dynamic_group.runtime.name} to use log-content in compartment id ${var.compartment_id}",
  ]
}

resource "oci_objectstorage_bucket" "run_records" {
  compartment_id        = var.compartment_id
  namespace             = var.object_storage_namespace
  name                  = "${var.name}-oci-runs-${substr(sha256(var.compartment_id), 0, 8)}"
  access_type           = "NoPublicAccess"
  object_events_enabled = false
  storage_tier          = "Standard"
  versioning            = "Enabled"
  auto_tiering          = "Disabled"
  freeform_tags         = local.tags
}

resource "oci_objectstorage_object" "projection" {
  for_each = local.controller_enabled ? var.execution_projections : {}

  namespace    = var.object_storage_namespace
  bucket       = oci_objectstorage_bucket.run_records.name
  object       = "projections/${each.key}.json"
  content      = jsonencode(each.value)
  content_type = "application/json"
}

resource "oci_functions_application" "controller" {
  count = local.controller_enabled ? 1 : 0

  compartment_id = var.compartment_id
  display_name   = "${var.name}-lifecycle"
  subnet_ids     = [oci_core_subnet.runtime.id]
  shape          = "GENERIC_X86"
  freeform_tags  = local.tags

  logging {
    line_format = "JSON"
  }
}

resource "oci_functions_function" "pipeline" {
  for_each = local.controller_enabled ? var.execution_projections : {}

  application_id                   = one(oci_functions_application.controller).id
  display_name                     = "${var.name}-${each.key}"
  image                            = var.controller_image
  image_digest                     = var.controller_image_digest
  memory_in_mbs                    = var.controller_memory_mib
  timeout_in_seconds               = 300
  detached_mode_timeout_in_seconds = 3600
  freeform_tags                    = merge(local.tags, { pipeline = each.key })
  config = {
    DANDER_OCI_NAMESPACE      = var.object_storage_namespace
    DANDER_OCI_RUN_BUCKET     = oci_objectstorage_bucket.run_records.name
    DANDER_OCI_PIPELINE       = each.key
    DANDER_OCI_PROJECTION_KEY = oci_objectstorage_object.projection[each.key].object
  }

  failure_destination {
    kind     = "NOTIFICATION"
    topic_id = oci_ons_notification_topic.runtime.id
  }
}

resource "oci_identity_dynamic_group" "controller" {
  count = local.controller_enabled ? 1 : 0

  compartment_id = var.tenancy_id
  name           = var.controller_dynamic_group_name
  description    = "Dander OCI lifecycle Function resource principals"
  matching_rule = format(
    "ANY {%s}",
    join(", ", [for function in oci_functions_function.pipeline : "resource.id='${function.id}'"]),
  )
  freeform_tags = local.tags
}

resource "oci_identity_policy" "controller" {
  count = local.controller_enabled ? 1 : 0

  compartment_id = var.tenancy_id
  name           = "${var.name}-lifecycle-controller"
  description    = "Least-privilege OCI lifecycle Function mutations"
  freeform_tags  = local.tags
  statements = [
    "Allow dynamic-group ${one(oci_identity_dynamic_group.controller).name} to manage compute-container-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group ${one(oci_identity_dynamic_group.controller).name} to use virtual-network-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group ${one(oci_identity_dynamic_group.controller).name} to manage objects in compartment id ${var.compartment_id} where target.bucket.name='${oci_objectstorage_bucket.run_records.name}'",
  ]
}

resource "oci_resource_scheduler_schedule" "pipeline" {
  for_each = local.controller_enabled ? var.execution_projections : {}

  action             = "START_RESOURCE"
  compartment_id     = var.compartment_id
  display_name       = "${var.name}-${each.key}"
  description        = "Invoke the Dander OCI lifecycle controller for ${each.key}"
  recurrence_type    = "CRON"
  recurrence_details = each.value.schedule.expression
  state              = each.value.schedule.paused ? "INACTIVE" : "ACTIVE"
  freeform_tags      = merge(local.tags, { pipeline = each.key })

  resources {
    id = oci_functions_function.pipeline[each.key].id

    parameters {
      parameter_type = "BODY"
      value          = [jsonencode({ action = "start", source = "resource_scheduler" })]
    }
  }
}

resource "oci_identity_dynamic_group" "scheduler" {
  count = local.controller_enabled ? 1 : 0

  compartment_id = var.tenancy_id
  name           = var.scheduler_dynamic_group_name
  description    = "Dander OCI Resource Scheduler invocations"
  matching_rule = format(
    "ANY {%s}",
    join(", ", [for id in local.resource_schedule_ids : "resource.id='${id}'"]),
  )
  freeform_tags = local.tags
}

resource "oci_identity_policy" "scheduler" {
  count = local.controller_enabled ? 1 : 0

  compartment_id = var.tenancy_id
  name           = "${var.name}-lifecycle-scheduler"
  description    = "Permit only Dander Resource Schedules to invoke lifecycle Functions"
  freeform_tags  = local.tags
  statements = [
    "Allow dynamic-group ${one(oci_identity_dynamic_group.scheduler).name} to use fn-invocation in compartment id ${var.compartment_id}",
  ]
}

resource "oci_events_rule" "container_lifecycle" {
  for_each = local.controller_enabled ? var.execution_projections : {}

  compartment_id = var.compartment_id
  display_name   = "${var.name}-${each.key}-container-lifecycle"
  description    = "Reconcile ${each.key} after its Container Instance lifecycle events"
  is_enabled     = true
  freeform_tags  = local.tags
  condition = jsonencode({
    data = {
      freeFormTags = {
        managed-by      = "dander"
        dander-pipeline = each.key
      }
    }
  })

  actions {
    action {
      action_type = "FAAS"
      is_enabled  = true
      function_id = oci_functions_function.pipeline[each.key].id
      description = "Reconcile ${each.key} after an OCI lifecycle event"
    }
  }
}

resource "oci_logging_log_group" "runtime" {
  compartment_id = var.compartment_id
  display_name   = "${var.name}-runtime"
  description    = "Dander Container Instance and lifecycle-controller logs"
  freeform_tags  = local.tags
}

resource "oci_ons_notification_topic" "runtime" {
  compartment_id = var.compartment_id
  name           = "${var.name}-runtime"
  description    = "Dander OCI execution and reconciliation alerts"
  freeform_tags  = local.tags
}

resource "oci_logging_log" "function_invocations" {
  count = local.controller_enabled ? 1 : 0

  display_name       = "${var.name}-lifecycle-invoke"
  log_group_id       = oci_logging_log_group.runtime.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30
  freeform_tags      = local.tags

  configuration {
    compartment_id = var.compartment_id

    source {
      category    = "invoke"
      resource    = one(oci_functions_application.controller).id
      service     = "functions"
      source_type = "OCISERVICE"
    }
  }
}

resource "oci_monitoring_alarm" "function_errors" {
  count = local.controller_enabled ? 1 : 0

  compartment_id        = var.compartment_id
  metric_compartment_id = var.compartment_id
  namespace             = "oci_faas"
  display_name          = "${var.name}-lifecycle-errors"
  body                  = "Dander lifecycle Function returned an error. Inspect the bounded invocation log and active run record."
  destinations          = [oci_ons_notification_topic.runtime.id]
  is_enabled            = true
  pending_duration      = "PT1M"
  query                 = "FunctionResponseCount[1m]{applicationId = \"${one(oci_functions_application.controller).id}\",responseType = \"Error\"}.sum() > 0"
  severity              = "ERROR"
  freeform_tags         = local.tags
}
