mock_provider "oci" {
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
  tenancy_id         = "ocid1.tenancy.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  compartment_id     = "ocid1.compartment.oc1..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  region             = "us-ashburn-1"
  dynamic_group_name = "dander_phase7_runtime"
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
      oci_kms_key.runtime.is_auto_rotation_enabled
    )
    error_message = "The runtime Vault must use the bounded-cost default tier and an auto-rotating key."
  }

  assert {
    condition = (
      oci_identity_dynamic_group.runtime.matching_rule == "ALL {resource.type='computecontainerinstance', resource.compartment.id='ocid1.compartment.oc1..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'}" &&
      length(oci_identity_policy.runtime.statements) == 4
    )
    error_message = "Only Container Instances in the selected compartment may receive runtime OCI permissions."
  }
}
