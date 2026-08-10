"""Read-only planning and verification for existing Kubernetes clusters."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Protocol, cast

import yaml

from dander.bootstrap.terraform import build_launcher_runtime
from dander.deployment import ResolvedTemplateRequest
from dander.project import ProjectConfigError, load_project_config
from dander.providers.kubernetes.chart import build_helm_values
from dander.providers.kubernetes.config import KubernetesLauncherConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.project import DanderProject


class KubernetesOperationError(RuntimeError):
    """Raised when a Kubernetes plan or read-only verification fails."""


class _Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _subprocess_runner(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool,
    capture_output: bool,
    text: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
    )


@dataclass(frozen=True, slots=True)
class KubernetesPlan:
    """Saved Helm values and manifests produced without cluster mutation."""

    chart: Path
    values: Path
    manifests: Path
    release_name: str
    namespace: str
    context: str


@dataclass(frozen=True, slots=True)
class KubernetesVerification:
    """Sanitized read-only comparison of one installed release."""

    context: str
    namespace: str
    service_account: str
    config_map: str
    pipelines: tuple[str, ...]
    image: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class KubernetesOperations:
    """Render Helm artifacts and verify a selected existing-cluster release."""

    def __init__(self, *, runner: _Runner | None = None) -> None:
        self._runner = runner or _subprocess_runner

    def plan(
        self,
        *,
        config: Path,
        deployment: str,
        image: str,
        output_dir: Path,
        platforms_config: Path | None = None,
    ) -> KubernetesPlan:
        """Lint and render the chart into saved review artifacts without an apply."""
        project_config = config.expanduser().resolve()
        try:
            _manifest, launcher_config, values = _resolve_release(
                project_config=project_config,
                platforms_config=platforms_config,
                deployment=deployment,
                image=image,
            )
        except (ProjectConfigError, ValueError) as error:
            raise KubernetesOperationError(str(error)) from error
        chart = project_config.parent / "infra" / "kubernetes" / "chart" / "dander"
        if not (chart / "Chart.yaml").is_file():
            raise KubernetesOperationError(f"Packaged Dander Helm chart is missing: {chart}")
        destination = output_dir.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        values_path = destination / f"{deployment}.values.yaml"
        manifests_path = destination / f"{deployment}.manifests.yaml"
        _atomic_write(values_path, yaml.safe_dump(values, sort_keys=False, width=100))
        self._execute(
            ("helm", "lint", str(chart), "--values", str(values_path)),
            cwd=project_config.parent,
        )
        rendered = self._execute(
            (
                "helm",
                "template",
                launcher_config.release_name,
                str(chart),
                "--namespace",
                launcher_config.namespace,
                "--values",
                str(values_path),
            ),
            cwd=project_config.parent,
        )
        _atomic_write(manifests_path, rendered)
        return KubernetesPlan(
            chart=chart,
            values=values_path,
            manifests=manifests_path,
            release_name=launcher_config.release_name,
            namespace=launcher_config.namespace,
            context=launcher_config.context,
        )

    def verify(
        self,
        *,
        config: Path,
        deployment: str,
        expected_image: str,
        platforms_config: Path | None = None,
    ) -> KubernetesVerification:
        """Verify service account, config, CronJobs, schedules, and image read-only."""
        project_config = config.expanduser().resolve()
        try:
            manifest, launcher, expected_values = _resolve_release(
                project_config=project_config,
                platforms_config=platforms_config,
                deployment=deployment,
                image=expected_image,
            )
        except (ProjectConfigError, ValueError) as error:
            raise KubernetesOperationError(str(error)) from error
        prefix = (
            "kubectl",
            "--context",
            launcher.context,
            "--namespace",
            launcher.namespace,
        )
        service_account = self._json(
            (*prefix, "get", "serviceaccount", launcher.service_account_name, "-o", "json"),
            cwd=project_config.parent,
        )
        _verify_service_account(service_account, launcher)
        config_map = f"{launcher.release_name}-config"
        config_payload = self._json(
            (*prefix, "get", "configmap", config_map, "-o", "json"),
            cwd=project_config.parent,
        )
        _verify_config_map(
            config_payload,
            deployment=manifest.deployment_name,
            profile=manifest.platform_name,
            image=expected_image,
        )
        secret = expected_values.get("existingSecret")
        secret_name = secret.get("name") if isinstance(secret, dict) else None
        if secret_name:
            self._execute(
                (*prefix, "get", "secret", str(secret_name), "-o", "json"),
                cwd=project_config.parent,
            )
        payload = self._json(
            (
                *prefix,
                "get",
                "cronjobs",
                "-l",
                f"app.kubernetes.io/instance={launcher.release_name}",
                "-o",
                "json",
            ),
            cwd=project_config.parent,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise KubernetesOperationError("kubectl returned an invalid CronJob list")
        observed: dict[str, dict[str, object]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise KubernetesOperationError("kubectl returned an invalid CronJob")
            metadata = item.get("metadata")
            if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
                raise KubernetesOperationError("kubectl returned a CronJob without a name")
            labels = metadata.get("labels")
            pipeline_id = labels.get("dander.io/pipeline") if isinstance(labels, dict) else None
            if not isinstance(pipeline_id, str):
                raise KubernetesOperationError("deployed CronJob is missing its pipeline label")
            observed[pipeline_id] = item
        expected_ids = set(manifest.terraform_pipelines())
        if set(observed) != expected_ids:
            raise KubernetesOperationError("deployed CronJobs do not match the selected pipelines")
        expected_pipelines = expected_values.get("pipelines")
        if not isinstance(expected_pipelines, dict):
            raise KubernetesOperationError("rendered Kubernetes pipeline values are invalid")
        for pipeline_id, item in observed.items():
            expected_pipeline = expected_pipelines.get(pipeline_id)
            if not isinstance(expected_pipeline, dict):
                raise KubernetesOperationError("rendered Kubernetes pipeline is invalid")
            spec = item.get("spec")
            if not isinstance(spec, dict):
                raise KubernetesOperationError("deployed CronJob has an invalid spec")
            _verify_cronjob(spec, expected_pipeline, launcher, expected_image)
        return KubernetesVerification(
            context=launcher.context,
            namespace=launcher.namespace,
            service_account=launcher.service_account_name,
            config_map=config_map,
            pipelines=tuple(sorted(observed)),
            image=expected_image,
        )

    def _json(self, args: Sequence[str], *, cwd: Path) -> dict[str, object]:
        output = self._execute(args, cwd=cwd)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise KubernetesOperationError("kubectl returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise KubernetesOperationError("kubectl returned an invalid response")
        return payload

    def _execute(self, args: Sequence[str], *, cwd: Path) -> str:
        executable = args[0]
        if shutil.which(executable) is None and self._runner is _subprocess_runner:
            raise KubernetesOperationError(f"{executable} is not installed or not on PATH")
        try:
            completed = self._runner(
                args,
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as error:
            raise KubernetesOperationError(
                f"{executable} is not installed or not on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or "").strip().splitlines()
            summary = detail[-1][:300] if detail else "command failed"
            raise KubernetesOperationError(f"{executable} failed: {summary}") from error
        return completed.stdout


def _resolve_release(
    *,
    project_config: Path,
    platforms_config: Path | None,
    deployment: str,
    image: str,
) -> tuple[DanderProject, KubernetesLauncherConfig, dict[str, object]]:
    manifest = load_project_config(
        project_config,
        platforms_path=platforms_config,
        deployment=deployment,
    )
    if manifest.launcher_provider != "kubernetes":
        raise ProjectConfigError(
            f"Deployment {deployment!r} does not select launcher.provider='kubernetes'"
        )
    if manifest.secret_provider != "environment":
        raise ProjectConfigError(
            "Kubernetes currently requires secrets.provider='environment' for "
            "operator-managed Secret references"
        )
    manifest.validate_references(project_config.parent)
    launcher_config = KubernetesLauncherConfig.model_validate(manifest.resolved_launcher_config())
    if manifest.platform.safety.require_guarded_free_tier:
        raise ProjectConfigError("Kubernetes cannot run the GCP guarded-free-tier preflight")
    launcher = build_launcher_runtime(launcher_config=manifest.resolved_launcher_config())
    templates = launcher.templates.build(
        ResolvedTemplateRequest(
            pipelines=manifest.terraform_pipelines(),
            image=image,
            profile_id=manifest.deployment_name,
            cpu=manifest.platform.runtime.cpu,
            memory=manifest.platform.runtime.memory,
            deadline_seconds=manifest.platform.runtime.timeout_seconds,
            launcher_retry_count=manifest.platform.runtime.max_retries,
            batch_rows=manifest.platform.runtime.batch_rows,
            alert_target=None,
        )
    )
    values = build_helm_values(
        launcher_config,
        templates,
        deployment_name=manifest.deployment_name,
        platform_name=manifest.platform_name,
    )
    return manifest, launcher_config, values


def _atomic_write(path: Path, content: str) -> None:
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(path)


def _verify_service_account(
    payload: dict[str, object],
    launcher: KubernetesLauncherConfig,
) -> None:
    metadata = _mapping(payload.get("metadata"), "deployed ServiceAccount has invalid metadata")
    annotations = _mapping(
        metadata.get("annotations", {}),
        "deployed ServiceAccount annotations are invalid",
    )
    if any(
        annotations.get(key) != value
        for key, value in launcher.workload_identity_annotations.items()
    ):
        raise KubernetesOperationError("deployed ServiceAccount annotations do not match")
    if payload.get("automountServiceAccountToken") is not False:
        raise KubernetesOperationError("deployed ServiceAccount unexpectedly mounts API tokens")


def _verify_config_map(
    payload: dict[str, object],
    *,
    deployment: str,
    profile: str,
    image: str,
) -> None:
    data = _mapping(payload.get("data"), "deployed ConfigMap data is invalid")
    expected = {
        "DANDER_DEPLOYMENT": deployment,
        "DANDER_PLATFORM_PROFILE": profile,
        "DANDER_IMAGE_REFERENCE": image,
    }
    if any(data.get(key) != value for key, value in expected.items()):
        raise KubernetesOperationError("deployed ConfigMap does not match the selected deployment")


def _verify_cronjob(
    spec: dict[str, object],
    expected: dict[str, object],
    launcher: KubernetesLauncherConfig,
    image: str,
) -> None:
    scalar_fields = (
        "schedule",
        "timeZone",
        "suspend",
        "successfulJobsHistoryLimit",
        "failedJobsHistoryLimit",
    )
    if any(spec.get(field) != expected.get(field) for field in scalar_fields):
        raise KubernetesOperationError("deployed CronJob schedule or history does not match")
    if spec.get("concurrencyPolicy") != "Forbid":
        raise KubernetesOperationError("deployed CronJob does not forbid overlap")
    job_template = _mapping(spec.get("jobTemplate"), "deployed CronJob has an invalid Job template")
    job_spec = _mapping(job_template.get("spec"), "deployed CronJob has an invalid Job spec")
    if any(
        job_spec.get(field) != expected.get(field)
        for field in ("backoffLimit", "activeDeadlineSeconds", "ttlSecondsAfterFinished")
    ):
        raise KubernetesOperationError("deployed Job retry, deadline, or TTL does not match")
    pod_template = _mapping(
        job_spec.get("template"), "deployed CronJob has an invalid pod template"
    )
    pod_spec = _mapping(pod_template.get("spec"), "deployed CronJob has an invalid pod spec")
    if (
        pod_spec.get("restartPolicy") != "Never"
        or pod_spec.get("serviceAccountName") != launcher.service_account_name
        or pod_spec.get("automountServiceAccountToken") is not False
        or pod_spec.get("terminationGracePeriodSeconds")
        != launcher.termination_grace_period_seconds
    ):
        raise KubernetesOperationError("deployed pod lifecycle or identity does not match")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise KubernetesOperationError("deployed CronJob must have one runtime container")
    container = _mapping(containers[0], "deployed runtime container is invalid")
    if container.get("image") != image or container.get("args") != expected.get("command"):
        raise KubernetesOperationError("deployed runtime image or command does not match")
    _verify_environment(container, expected, launcher)
    _verify_resources(container, expected)


def _verify_environment(
    container: dict[str, object],
    expected: dict[str, object],
    launcher: KubernetesLauncherConfig,
) -> None:
    environment = container.get("env")
    if not isinstance(environment, list):
        raise KubernetesOperationError("deployed runtime environment is invalid")
    observed_values: dict[str, str] = {}
    observed_secrets: dict[str, tuple[str, str]] = {}
    for raw_item in environment:
        item = _mapping(raw_item, "deployed runtime environment is invalid")
        name = item.get("name")
        if not isinstance(name, str):
            raise KubernetesOperationError("deployed runtime environment name is invalid")
        value = item.get("value")
        if isinstance(value, str):
            observed_values[name] = value
            continue
        value_from = _mapping(item.get("valueFrom"), "deployed runtime secret reference is invalid")
        secret_ref = _mapping(
            value_from.get("secretKeyRef"),
            "deployed runtime secret reference is invalid",
        )
        secret_name = secret_ref.get("name")
        secret_key = secret_ref.get("key")
        if not isinstance(secret_name, str) or not isinstance(secret_key, str):
            raise KubernetesOperationError("deployed runtime secret reference is invalid")
        observed_secrets[name] = (secret_name, secret_key)
    expected_values = _expected_pairs(expected.get("environment"), value_key="value")
    expected_keys = _expected_pairs(expected.get("secretEnvironment"), value_key="key")
    expected_secrets = {
        name: (str(launcher.existing_secret_name), key) for name, key in expected_keys.items()
    }
    if observed_values != expected_values or observed_secrets != expected_secrets:
        raise KubernetesOperationError("deployed runtime environment references do not match")


def _expected_pairs(value: object, *, value_key: str) -> dict[str, str]:
    if not isinstance(value, list):
        raise KubernetesOperationError("rendered Kubernetes environment is invalid")
    result: dict[str, str] = {}
    for raw_item in value:
        item = _mapping(raw_item, "rendered Kubernetes environment is invalid")
        name = item.get("name")
        item_value = item.get(value_key)
        if not isinstance(name, str) or not isinstance(item_value, str):
            raise KubernetesOperationError("rendered Kubernetes environment is invalid")
        result[name] = item_value
    return result


def _verify_resources(container: dict[str, object], expected: dict[str, object]) -> None:
    resources = _mapping(container.get("resources"), "deployed runtime resources are invalid")
    expected_resources = _mapping(
        expected.get("resources"), "rendered runtime resources are invalid"
    )
    for group in ("requests", "limits"):
        actual_group = _mapping(resources.get(group), "deployed runtime resources are invalid")
        expected_group = _mapping(
            expected_resources.get(group), "rendered runtime resources are invalid"
        )
        for name, value in expected_group.items():
            if _resource_quantity(name, actual_group.get(name)) != _resource_quantity(name, value):
                raise KubernetesOperationError("deployed runtime resources do not match")


def _resource_quantity(name: str, value: object) -> int:
    if not isinstance(value, str):
        raise KubernetesOperationError("deployed runtime resource quantity is invalid")
    try:
        if name == "cpu":
            return int(value.removesuffix("m")) if value.endswith("m") else int(value) * 1_000
        if value.endswith("Mi"):
            return int(value.removesuffix("Mi"))
        if value.endswith("Gi"):
            return int(value.removesuffix("Gi")) * 1_024
    except ValueError as error:
        raise KubernetesOperationError("deployed runtime resource quantity is invalid") from error
    raise KubernetesOperationError("deployed runtime resource quantity is unsupported")


def _mapping(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise KubernetesOperationError(message)
    return cast("dict[str, object]", value)


__all__ = [
    "KubernetesOperationError",
    "KubernetesOperations",
    "KubernetesPlan",
    "KubernetesVerification",
]
