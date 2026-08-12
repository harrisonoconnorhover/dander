locals {
  tags = merge(var.freeform_tags, {
    component  = "oci-container-instances"
    managed-by = "dander"
  })
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
