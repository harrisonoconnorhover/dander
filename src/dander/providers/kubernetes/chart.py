"""Deterministic Helm values for Kubernetes execution projections."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from dander.deployment import ExecutionProjectionError, ExecutionTemplate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.providers.kubernetes.config import KubernetesLauncherConfig

_RESOURCE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,50}[a-z0-9])?$")


def build_helm_values(
    config: KubernetesLauncherConfig,
    templates: Mapping[str, ExecutionTemplate],
    *,
    deployment_name: str,
    platform_name: str,
) -> dict[str, object]:
    """Render non-secret, deterministic chart inputs for one deployment."""
    if not templates:
        raise ExecutionProjectionError("Kubernetes deployment requires at least one pipeline")
    images = {template.image for template in templates.values()}
    selectors = {template.profile_id for template in templates.values()}
    if len(images) != 1 or len(selectors) != 1:
        raise ExecutionProjectionError("Kubernetes pipelines must share one image and deployment")
    pipelines: dict[str, object] = {}
    for pipeline_id, template in sorted(templates.items()):
        if template.launcher != "kubernetes":
            raise ExecutionProjectionError("Helm values require Kubernetes templates")
        secret_environment = [
            {"name": name, "key": reference.reference.removeprefix("env://")}
            for name, reference in template.secret_bindings
        ]
        labels = {
            key.replace("_", "-"): _label_value(value)
            for key, value in template.labels
            if key not in {"image_digest", "dander_version"}
        }
        pipelines[pipeline_id] = {
            "resourceName": kubernetes_resource_name(config.release_name, pipeline_id),
            "command": list(template.command),
            "environment": [{"name": name, "value": value} for name, value in template.environment],
            "secretEnvironment": secret_environment,
            "schedule": template.schedule.expression,
            "timeZone": template.schedule.time_zone,
            "suspend": template.schedule.paused,
            "backoffLimit": template.resources.launcher_retry_count,
            "activeDeadlineSeconds": template.resources.deadline_seconds,
            "ttlSecondsAfterFinished": config.ttl_seconds_after_finished,
            "successfulJobsHistoryLimit": config.successful_jobs_history_limit,
            "failedJobsHistoryLimit": config.failed_jobs_history_limit,
            "resources": {
                "requests": {
                    "cpu": f"{template.resources.cpu_millis}m",
                    "memory": f"{template.resources.memory_mib}Mi",
                    "ephemeral-storage": (f"{template.resources.ephemeral_storage_mib or 1}Mi"),
                },
                "limits": {
                    "cpu": f"{template.resources.cpu_millis}m",
                    "memory": f"{template.resources.memory_mib}Mi",
                    "ephemeral-storage": (f"{template.resources.ephemeral_storage_mib or 1}Mi"),
                },
            },
            "labels": dict(sorted(labels.items())),
        }
    image = next(iter(images))
    return {
        "deployment": deployment_name,
        "profile": platform_name,
        "image": {"reference": image},
        "serviceAccount": {
            "create": True,
            "name": config.service_account_name,
            "annotations": config.workload_identity_annotations,
        },
        "existingSecret": {"name": config.existing_secret_name or ""},
        "podLabels": config.pod_labels,
        "terminationGracePeriodSeconds": config.termination_grace_period_seconds,
        "configMap": {"keepOnUninstall": False},
        "rbac": {"create": False},
        "pipelines": pipelines,
    }


def kubernetes_resource_name(release_name: str, pipeline_id: str) -> str:
    """Return one stable CronJob-safe name no longer than 52 characters."""
    base = re.sub(r"[^a-z0-9-]", "-", f"{release_name}-{pipeline_id}".lower()).strip("-")
    if len(base) <= 52 and _RESOURCE_NAME.fullmatch(base):
        return base
    digest = hashlib.sha256(base.encode()).hexdigest()[:8]
    prefix = base[:43].rstrip("-")
    name = f"{prefix}-{digest}"
    if _RESOURCE_NAME.fullmatch(name) is None:
        raise ExecutionProjectionError("pipeline cannot produce a safe Kubernetes resource name")
    return name


def _label_value(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip("-_.")
    if len(normalized) <= 63:
        return normalized
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{normalized[:54].rstrip('-_.')}-{digest}"


__all__ = ["build_helm_values", "kubernetes_resource_name"]
