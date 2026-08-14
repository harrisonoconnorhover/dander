data "google_project" "current" {
  project_id = var.project_id
}

locals {
  control_url = "https://${var.control_service_name}-${var.project_number}.${var.region}.run.app"
  druff_url   = "https://${var.druff_service_name}-${var.project_number}.${var.region}.run.app"
  configs = {
    control-oidc = {
      service_account = google_service_account.control.email
      data            = var.control_oidc_json
      filename        = "control-oidc.json"
    }
    graph-store = {
      service_account = google_service_account.control.email
      data            = var.graph_store_json
      filename        = "control-graph-store.json"
    }
    druff-bootstrap = {
      service_account = google_service_account.druff.email
      data            = var.bootstrap_json
      filename        = "bootstrap.json"
    }
    druff-caddy = {
      service_account = google_service_account.druff.email
      data            = var.druff_caddyfile
      filename        = "Caddyfile"
    }
  }
}

check "project_number_matches" {
  assert {
    condition     = data.google_project.current.number == var.project_number
    error_message = "project_number does not match the selected GCP project."
  }
}

check "deterministic_urls_fit_dns" {
  assert {
    condition = (
      length("${var.control_service_name}-${var.project_number}") <= 63 &&
      length("${var.druff_service_name}-${var.project_number}") <= 63
    )
    error_message = "Cloud Run deterministic URL DNS segments must not exceed 63 characters."
  }
}

resource "google_storage_bucket" "graphs" {
  project                     = var.project_id
  name                        = var.graph_bucket
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # This bucket is disposable and its live proof must leave no recoverable graph data. The
  # retained Terraform-state bucket deliberately keeps its existing recovery policy.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  labels = {
    managed-by = "dander"
    phase      = "d7"
    purpose    = "control-graph-store"
  }
}

resource "google_service_account" "control" {
  project      = var.project_id
  account_id   = var.control_service_name
  display_name = "Dander D7 Control service"
}

resource "google_service_account" "druff" {
  project      = var.project_id
  account_id   = var.druff_service_name
  display_name = "Dander D7 Druff service"
}

resource "google_storage_bucket_iam_member" "control_objects" {
  bucket = google_storage_bucket.graphs.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.control.email}"
}

resource "google_secret_manager_secret" "config" {
  for_each  = local.configs
  project   = var.project_id
  secret_id = "${var.control_service_name}-${each.key}"

  replication {
    auto {}
  }

  labels = {
    managed-by = "dander"
    phase      = "d7"
    purpose    = "startup-config"
  }
}

resource "google_secret_manager_secret_version" "config" {
  for_each    = local.configs
  secret      = google_secret_manager_secret.config[each.key].id
  secret_data = each.value.data
}

resource "google_secret_manager_secret_iam_member" "config" {
  for_each  = local.configs
  project   = var.project_id
  secret_id = google_secret_manager_secret.config[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.service_account}"
}

resource "google_cloud_run_v2_service" "control" {
  project              = var.project_id
  name                 = var.control_service_name
  location             = var.region
  deletion_protection  = false
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true

  labels = {
    managed-by = "dander"
    phase      = "d7"
    component  = "control"
  }

  scaling {
    manual_instance_count = 0
    min_instance_count    = 0
  }

  template {
    service_account                  = google_service_account.control.email
    timeout                          = "300s"
    max_instance_request_concurrency = 10

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    volumes {
      name = "control-oidc"
      secret {
        secret = google_secret_manager_secret.config["control-oidc"].secret_id
        items {
          version = google_secret_manager_secret_version.config["control-oidc"].version
          path    = "control-oidc.json"
          mode    = 292
        }
      }
    }

    volumes {
      name = "graph-store"
      secret {
        secret = google_secret_manager_secret.config["graph-store"].secret_id
        items {
          version = google_secret_manager_secret_version.config["graph-store"].version
          path    = "control-graph-store.json"
          mode    = 292
        }
      }
    }

    containers {
      image = var.dander_image
      args  = var.control_args

      ports {
        name           = "http1"
        container_port = 8770
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = false
      }

      volume_mounts {
        name       = "control-oidc"
        mount_path = "/etc/dander/oidc"
      }

      volume_mounts {
        name       = "graph-store"
        mount_path = "/etc/dander/graph-store"
      }

      startup_probe {
        failure_threshold = 12
        period_seconds    = 5
        timeout_seconds   = 2

        http_get {
          path = "/readyz"
          port = 8770
        }
      }

      liveness_probe {
        failure_threshold = 3
        period_seconds    = 30
        timeout_seconds   = 2

        http_get {
          path = "/healthz"
          port = 8770
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.config,
    google_storage_bucket_iam_member.control_objects,
  ]
}

resource "google_cloud_run_v2_service" "druff" {
  project              = var.project_id
  name                 = var.druff_service_name
  location             = var.region
  deletion_protection  = false
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true

  labels = {
    managed-by = "dander"
    phase      = "d7"
    component  = "druff"
  }

  scaling {
    manual_instance_count = 0
    min_instance_count    = 0
  }

  template {
    service_account                  = google_service_account.druff.email
    timeout                          = "60s"
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    volumes {
      name = "druff-bootstrap"
      secret {
        secret = google_secret_manager_secret.config["druff-bootstrap"].secret_id
        items {
          version = google_secret_manager_secret_version.config["druff-bootstrap"].version
          path    = "bootstrap.json"
          mode    = 292
        }
      }
    }

    volumes {
      name = "druff-caddy"
      secret {
        secret = google_secret_manager_secret.config["druff-caddy"].secret_id
        items {
          version = google_secret_manager_secret_version.config["druff-caddy"].version
          path    = "Caddyfile"
          mode    = 292
        }
      }
    }

    containers {
      image   = var.druff_image
      command = ["/usr/bin/caddy"]
      args    = ["run", "--config", "/etc/dander/caddy/Caddyfile", "--adapter", "caddyfile"]

      ports {
        name           = "http1"
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = false
      }

      volume_mounts {
        name       = "druff-bootstrap"
        mount_path = "/etc/dander/bootstrap"
      }

      volume_mounts {
        name       = "druff-caddy"
        mount_path = "/etc/dander/caddy"
      }

      startup_probe {
        failure_threshold = 12
        period_seconds    = 5
        timeout_seconds   = 2

        http_get {
          path = "/readyz"
          port = 8080
        }
      }

      liveness_probe {
        failure_threshold = 3
        period_seconds    = 30
        timeout_seconds   = 2

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.config]
}
