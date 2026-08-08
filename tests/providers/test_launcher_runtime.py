"""Launcher-provider selection and projection parity coverage."""

from __future__ import annotations

import sys

from dander.deployment import LauncherRuntime, build_gcp_execution_templates
from dander.providers import ProviderKind, default_provider_registry

_IMAGE = "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64
_PIPELINES: dict[str, dict[str, object]] = {
    "greenhouse_jobs": {
        "runtime_service_account_id": "dander-runtime",
        "build_models": True,
        "schedule": "0 9 * * *",
        "time_zone": "America/New_York",
        "paused": True,
        "secret_env": {},
    }
}


def _templates(runtime: LauncherRuntime) -> dict[str, object]:
    return {
        pipeline_id: template.as_dict()
        for pipeline_id, template in runtime.templates.build(
            _PIPELINES,
            image=_IMAGE,
            project="unit-project",
            cpu=1,
            memory="512Mi",
            deadline_seconds=300,
            launcher_retry_count=1,
            batch_rows=10_000,
            require_guarded_free_tier=False,
            alert_target="operator@example.invalid",
        ).items()
    }


def test_cloud_run_factory_is_lazy_and_matches_the_accepted_projection() -> None:
    module_name = "dander.providers.cloud_run.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.LAUNCHER,
        {"provider": "cloud_run", "region": "us-central1"},
    )

    assert module_name not in sys.modules
    runtime = registry.build(ProviderKind.LAUNCHER, config)

    assert isinstance(runtime, LauncherRuntime)
    assert runtime.provider_id == "cloud_run"
    assert runtime.region == "us-central1"
    assert runtime.capabilities.supports_schedules is True
    assert module_name in sys.modules
    expected = {
        pipeline_id: template.as_dict()
        for pipeline_id, template in build_gcp_execution_templates(
            _PIPELINES,
            image=_IMAGE,
            project="unit-project",
            cpu=1,
            memory="512Mi",
            deadline_seconds=300,
            launcher_retry_count=1,
            batch_rows=10_000,
            require_guarded_free_tier=False,
            alert_target="operator@example.invalid",
        ).items()
    }
    assert _templates(runtime) == expected
