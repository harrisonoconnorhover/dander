mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
}

variables {
  project_id                = "dander-unit-project"
  project_number            = "123456789012"
  region                    = "us-central1"
  bootstrap_service_account = "dander-bootstrap@dander-unit-project.iam.gserviceaccount.com"
  graph_bucket              = "dander-unit-control-graphs"
  dander_image              = "us-central1-docker.pkg.dev/dander-unit-project/dander/control@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  druff_image               = "us-central1-docker.pkg.dev/dander-unit-project/dander/druff@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  control_args              = ["control", "serve", "--host", "0.0.0.0", "--port", "8770", "--oidc-config", "/etc/dander/oidc/control-oidc.json", "--graph-store-config", "/etc/dander/graph-store/control-graph-store.json"]
  control_oidc_json         = "{\"api_url\":\"https://control.example.test\"}\n"
  graph_store_json          = "{\"bucket\":\"dander-unit-control-graphs\",\"kind\":\"gcs\"}\n"
  bootstrap_json            = "{\"api_url\":\"https://control.example.test\"}\n"
  druff_caddyfile           = ":8080 { root * /app file_server }\n"
}

run "projects_private_keyless_cloud_run_profile" {
  command = plan

  assert {
    condition = (
      google_storage_bucket.graphs.uniform_bucket_level_access &&
      google_storage_bucket.graphs.public_access_prevention == "enforced" &&
      google_storage_bucket.graphs.versioning[0].enabled &&
      google_storage_bucket.graphs.soft_delete_policy[0].retention_duration_seconds == 0 &&
      google_storage_bucket.graphs.force_destroy
    )
    error_message = "The disposable GraphStore must be private, versioned, and leave no soft-deleted data."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.control_objects.role == "roles/storage.objectUser" &&
      google_service_account.control.account_id == "dander-control-d7" &&
      google_service_account.druff.account_id == "druff-control-d7" &&
      google_cloud_run_v2_service.control.invoker_iam_disabled &&
      google_cloud_run_v2_service.druff.invoker_iam_disabled
    )
    error_message = "Control and Druff must remain public with distinct narrow workload identities."
  }

  assert {
    condition = (
      length(google_secret_manager_secret_version.config) == 4 &&
      length(google_cloud_run_v2_service.druff.template[0].containers[0].command) == 1 &&
      google_cloud_run_v2_service.druff.template[0].containers[0].command[0] == "/usr/bin/caddy" &&
      alltrue([
        for mount in google_cloud_run_v2_service.druff.template[0].containers[0].volume_mounts :
        mount.mount_path != "/app" && !startswith(mount.mount_path, "/app/")
      ])
    )
    error_message = "Numeric startup config mounts must not hide the immutable Druff export."
  }

  assert {
    condition = (
      google_cloud_run_v2_service.control.template[0].containers[0].args == var.control_args
    )
    error_message = "Cloud Run Control must execute the exact D6-projected command."
  }
}
