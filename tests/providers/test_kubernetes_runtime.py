"""Kubernetes launcher projection and fail-closed configuration tests."""

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from dander.deployment import ExecutionProjectionError, LauncherRuntime
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.kubernetes import KubernetesLauncherConfig

_IMAGE = "ghcr.io/example/dander@sha256:" + "a" * 64
_PIPELINES: dict[str, dict[str, object]] = {
    "salesforce_crm": {
        "runtime_service_account_id": "dander-runtime",
        "build_models": True,
        "schedule": "0 9 * * *",
        "time_zone": "America/New_York",
        "paused": True,
        "secret_env": {"DANDER_POSTGRES_DSN": "postgres-dsn"},
    }
}


def _runtime(*, secret_name: str | None = "dander-runtime") -> LauncherRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        {
            "provider": "kubernetes",
            "context": "kind-dander",
            "namespace": "dander-test",
            "release_name": "dander",
            "service_account_name": "dander-runtime",
            "existing_secret_name": secret_name,
            "workload_identity_annotations": {
                "eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/dander"
            },
            "pod_labels": {"team": "analytics"},
        },
    )
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    return runtime


def test_kubernetes_factory_is_lazy_and_projects_native_profile() -> None:
    module_name = "dander.providers.kubernetes.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        {
            "provider": "kubernetes",
            "context": "kind-dander",
            "existing_secret_name": "dander-runtime",
            "pod_labels": {"team": "analytics"},
        },
    )

    assert module_name not in sys.modules
    runtime = registry.build(ProviderKind.LAUNCHER, config)
    assert isinstance(runtime, LauncherRuntime)
    assert module_name in sys.modules
    template = runtime.templates.build(
        _PIPELINES,
        image=_IMAGE,
        project="",
        cpu=1,
        memory="512Mi",
        deadline_seconds=900,
        launcher_retry_count=1,
        batch_rows=100,
        require_guarded_free_tier=False,
        alert_target=None,
        profile_id="postgres",
    )["salesforce_crm"]

    assert template.launcher == "kubernetes"
    assert template.profile_id == "postgres"
    assert template.workload_identity == "kubernetes://dander/serviceaccounts/dander-runtime"
    assert template.command[:8] == (
        "runtime",
        "execute",
        "--contract",
        "io.dander.runtime/v1",
        "--pipeline",
        "salesforce_crm",
        "--platform",
        "postgres",
    )
    assert template.resources.ephemeral_storage_mib == 1_024
    assert template.observability.log_destination == "stdout"
    assert dict(template.secret_bindings)["DANDER_POSTGRES_DSN"].reference == ("env://postgres-dsn")
    assert dict(template.labels)["profile"] == "postgres"


@pytest.mark.parametrize(
    ("secret_name", "guarded", "alert_target", "message"),
    [
        (None, False, None, "existing_secret_name"),
        ("dander-runtime", True, None, "guarded-free-tier"),
        ("dander-runtime", False, "operator@example.invalid", "alert target"),
    ],
)
def test_kubernetes_rejects_intent_the_chart_cannot_honor(
    secret_name: str | None,
    guarded: bool,
    alert_target: str | None,
    message: str,
) -> None:
    with pytest.raises(ExecutionProjectionError, match=message):
        _runtime(secret_name=secret_name).templates.build(
            _PIPELINES,
            image=_IMAGE,
            project="",
            cpu=1,
            memory="512Mi",
            deadline_seconds=900,
            launcher_retry_count=1,
            batch_rows=100,
            require_guarded_free_tier=guarded,
            alert_target=alert_target,
            profile_id="postgres",
        )


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "kubernetes", "context": "kind-test", "namespace": "not.valid"},
        {
            "provider": "kubernetes",
            "context": "kind-test",
            "pod_labels": {"app.kubernetes.io/name": "override"},
        },
    ],
)
def test_kubernetes_rejects_invalid_or_reserved_metadata(config: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        KubernetesLauncherConfig.model_validate(config)
