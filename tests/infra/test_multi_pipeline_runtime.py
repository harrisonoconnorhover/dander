"""Static Terraform contracts for additive hosted pipelines."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_root_passes_pipeline_map_and_scopes_secrets_per_runtime() -> None:
    root = (ROOT / "infra/main.tf").read_text(encoding="utf-8")
    secret_module = (ROOT / "infra/modules/secret-manager/main.tf").read_text(encoding="utf-8")
    normalized = "\n".join(" ".join(line.split()) for line in root.splitlines())

    assert "pipelines = var.pipelines" in normalized
    assert "execution_projections = var.execution_projections" in normalized
    assert "failure_alert_email = var.failure_alert_email" in normalized
    assert "runtime_cpu = var.runtime_cpu" in normalized
    assert "runtime_memory = var.runtime_memory" in normalized
    assert "runtime_timeout_seconds = var.runtime_timeout_seconds" in normalized
    assert "runtime_max_retries = var.runtime_max_retries" in normalized
    assert "runtime_batch_rows = var.runtime_batch_rows" in normalized
    assert "require_guarded_free_tier = var.require_guarded_free_tier" in normalized
    assert "pipeline_secret_accessors" in root
    assert "accessors_by_secret = local.pipeline_secret_accessors" in root
    assert "setproduct(var.secret_ids" not in secret_module
    assert "depends_on = [module.bigquery]" in root
    assert 'toset(["raw"])' in root


def test_scheduled_module_preserves_greenhouse_and_creates_each_pipeline() -> None:
    module = (ROOT / "infra/modules/scheduled-job/main.tf").read_text(encoding="utf-8")
    normalized = "\n".join(" ".join(line.split()) for line in module.splitlines())

    assert 'to   = google_cloud_run_v2_job.ingestion["greenhouse_jobs"]' in module
    assert 'to   = google_cloud_scheduler_job.ingestion["greenhouse_jobs"]' in module
    assert 'resource "google_cloud_run_v2_job" "ingestion" {' in module
    assert 'resource "google_cloud_scheduler_job" "ingestion" {' in module
    assert 'resource "google_monitoring_notification_channel" "pipeline_failures" {' in module
    assert 'resource "google_monitoring_alert_policy" "pipeline_failure" {' in module
    assert 'metric.type = \\"run.googleapis.com/job/completed_execution_count\\"' in module
    assert 'metric.label.\\"result\\" = \\"failed\\"' in module
    assert module.count("for_each = var.pipelines") >= 7
    assert (
        "guarded_runtime_pipelines = var.require_guarded_free_tier ? var.pipelines : {}"
        in normalized
    )
    assert module.count("for_each = local.guarded_runtime_pipelines") == 2
    assert "resources.deadline_seconds}s" in normalized
    assert "resources.launcher_retry_count" in normalized
    assert "resources.cpu_millis / 1000" in normalized
    assert "resources.memory_mib}Mi" in normalized
    assert "args = var.execution_projections[each.key].command" in normalized
    assert "secret_bindings" in normalized
    assert 'timeout = "300s"' not in normalized
    assert "max_retries = 1" not in normalized
    assert 'cpu = "1"' not in normalized
    assert 'memory = "512Mi"' not in normalized


def test_unguarded_runtime_omits_guard_resources_but_keeps_hosted_platform() -> None:
    root = (ROOT / "infra/main.tf").read_text(encoding="utf-8")
    module = (ROOT / "infra/modules/scheduled-job/main.tf").read_text(encoding="utf-8")
    normalized_root = "\n".join(" ".join(line.split()) for line in root.splitlines())
    normalized_module = "\n".join(" ".join(line.split()) for line in module.splitlines())

    assert "count = var.enable_cost_guard ? 1 : 0" in normalized_root
    assert (
        "guarded_runtime_pipelines = var.require_guarded_free_tier ? var.pipelines : {}"
        in normalized_module
    )
    for resource in (
        'resource "google_billing_account_iam_member" "runtime_budget_viewer"',
        'resource "google_project_iam_member" "runtime_pubsub_viewer"',
    ):
        guarded_resource = module.split(resource, maxsplit=1)[1].split("resource ", maxsplit=1)[0]
        assert "for_each = local.guarded_runtime_pipelines" in guarded_resource
    for resource in (
        'resource "google_service_account" "runtime"',
        'resource "google_service_account" "scheduler"',
        'resource "google_bigquery_dataset_iam_member" "runtime_writer"',
        'resource "google_cloud_run_v2_job" "ingestion"',
        'resource "google_cloud_scheduler_job" "ingestion"',
        'resource "google_monitoring_alert_policy" "pipeline_failure"',
    ):
        assert resource in module
    assert "args = var.execution_projections[each.key].command" in normalized_module
    assert 'module "bigquery"' in root
    assert 'module "secret_manager"' in root


def test_container_carries_the_project_manifest() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY dander.yaml ./dander.yaml" in dockerfile
    assert "COPY examples ./examples" in dockerfile
    assert "COPY infra ./infra" in dockerfile
    assert "!dander.yaml" in dockerignore
    assert "!examples/**" in dockerignore
    assert "!infra/**" in dockerignore
    assert "infra/.terraform/" in dockerignore


def test_container_declares_the_oci_runtime_artifact_contract() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "org.opencontainers.image.source=" in dockerfile
    assert "org.opencontainers.image.documentation=" in dockerfile
    assert "org.opencontainers.image.licenses=" in dockerfile
    assert "org.opencontainers.image.revision=" in dockerfile
    assert "org.opencontainers.image.created=" in dockerfile
    assert "DANDER_BUILD_REVISION" in dockerfile
    assert "DANDER_BUILD_CREATED" in dockerfile
    assert "util-linux" in dockerfile
    assert "USER 65532:65532" in dockerfile


def test_bootstrap_identity_can_manage_monitoring_alerts() -> None:
    bootstrap = (ROOT / "infra/bootstrap-admin/main.tf").read_text(encoding="utf-8")

    assert '"monitoring.googleapis.com"' in bootstrap
    assert '"roles/monitoring.editor"' in bootstrap
