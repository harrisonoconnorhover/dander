"""Provider-free runtime inspection and local lifecycle conformance."""

from __future__ import annotations

import json
import signal
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from dander import __version__
from dander.executor import PipelineExecutor
from dander.ingestion import SourceConfig
from dander.plugins import ConnectorPluginRegistry, load_connector_plugins
from dander.project import load_project_config
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.runtime_contract import (
    RUNTIME_CONTRACT,
    LauncherContext,
    RuntimeCancelledError,
    RuntimeContractError,
    RuntimeEvent,
    graceful_signal_handlers,
)
from dander.state import RunStatus, SqliteRunHistoryStore

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from dander.concurrency import OwnershipGuard

_PLATFORM_PROFILES = ("gcp",)
_LAUNCHERS = ("cloud_run", "local")
_WAREHOUSES = ("bigquery",)
_STATE_BACKENDS = ("bigquery", "sqlite")
_CATALOGS = ("dataplex", "local_json", "none")
_SECRET_PROVIDERS = ("environment", "gcp_secret_manager")


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    """Non-secret installed runtime and plugin metadata."""

    contract: str
    build: Mapping[str, object]
    adapters: Mapping[str, object]
    plugins: tuple[Mapping[str, object], ...]

    def to_json(self) -> str:
        """Render deterministic inspection JSON for humans and launchers."""
        return json.dumps(
            {
                "contract": self.contract,
                "build": self.build,
                "adapters": self.adapters,
                "plugins": self.plugins,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class RuntimeConformance:
    """Normalized result of the credential-free local lifecycle probe."""

    contract: str
    status: str
    run_id: str
    filesystem_writes: tuple[str, ...]
    signal: str
    events: tuple[str, ...]

    def to_json(self) -> str:
        """Render one deterministic conformance report."""
        return json.dumps(
            {
                "contract": self.contract,
                "status": self.status,
                "run_id": self.run_id,
                "filesystem_writes": self.filesystem_writes,
                "signal": self.signal,
                "events": self.events,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


def inspect_runtime(config: Path) -> RuntimeInspection:
    """Inspect configured packages without constructing sources or contacting providers."""
    manifest = load_project_config(config)
    registry = load_connector_plugins(manifest.plugins)
    return RuntimeInspection(
        contract=RUNTIME_CONTRACT,
        build={
            "distribution": "dander-platform",
            "version": __version__,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
        adapters={
            "platform_profiles": _PLATFORM_PROFILES,
            "launchers": _LAUNCHERS,
            "warehouses": _WAREHOUSES,
            "state_backends": _STATE_BACKENDS,
            "catalogs": _CATALOGS,
            "secret_providers": _SECRET_PROVIDERS,
            "ingestion_engines": registry.engines,
        },
        plugins=_plugin_metadata(registry),
    )


def run_local_conformance(work_dir: Path) -> RuntimeConformance:
    """Run one deterministic executor lifecycle using only local SQLite state."""
    work_dir.mkdir(parents=True, exist_ok=True)
    state_path = work_dir / "state.db"
    if state_path.exists():
        raise RuntimeContractError(f"conformance state already exists: {state_path}")

    before = _relative_files(work_dir)
    run_id = f"runtime-conformance-{uuid4().hex}"
    source_config = SourceConfig(
        name="runtime_conformance",
        base_url="https://runtime-conformance.invalid",
        auth_strategy="none",
        endpoints=[],
    )
    history = SqliteRunHistoryStore(state_path)
    result = PipelineExecutor(
        pipeline_id="runtime_conformance",
        source_config=source_config,
        ingestion=_ConformanceIngestion(),
        history=history,
        project="runtime-conformance",
        models_dir=work_dir,
        selected_models=None,
        build_models=False,
    ).execute(run_id=run_id)
    record = history.recent(pipeline_id="runtime_conformance", limit=1)[0]
    if record.status is not RunStatus.SUCCEEDED or record.run_id != run_id:
        raise RuntimeContractError("local conformance run history did not finish successfully")

    context = LauncherContext.from_environment(
        {
            "DANDER_RUN_ID": run_id,
            "DANDER_LAUNCHER": "local_conformance",
        }
    )
    started = RuntimeEvent.started(
        context=context,
        pipeline_id="runtime_conformance",
        platform="gcp",
    )
    completed = RuntimeEvent.completed(result, context=context, platform="gcp")
    for event in (started, completed):
        json.loads(event.to_json())

    observed_signal = _probe_signal_translation()
    writes = tuple(sorted(_relative_files(work_dir) - before))
    if writes != ("state.db",):
        raise RuntimeContractError(
            "local conformance wrote outside its one declared SQLite state file"
        )
    return RuntimeConformance(
        contract=RUNTIME_CONTRACT,
        status="succeeded",
        run_id=run_id,
        filesystem_writes=writes,
        signal=observed_signal,
        events=(started.event, completed.event),
    )


class _ConformanceIngestion:
    def run(
        self,
        *,
        run_id: str | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> PipelineRunResult:
        del ownership
        if run_id is None:
            raise RuntimeContractError("conformance executor did not supply a run id")
        return PipelineRunResult(
            run_id=run_id,
            source="runtime_conformance",
            endpoints=(
                EndpointRunResult(
                    endpoint="fixture",
                    extracted=2,
                    affected=2,
                    committed_cursor=None,
                ),
            ),
        )


def _plugin_metadata(registry: ConnectorPluginRegistry) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "plugin_id": installed.plugin.plugin_id,
            "api_version": installed.plugin.api_version,
            "engine": installed.plugin.engine,
            "display_name": installed.plugin.display_name,
            "distribution": installed.distribution,
            "version": installed.version,
            "connectors": tuple(
                {
                    "connector_id": connector.connector_id,
                    "display_name": connector.display_name,
                    "endpoints": tuple(endpoint.endpoint_id for endpoint in connector.endpoints),
                }
                for connector in installed.plugin.connectors
            ),
        }
        for installed in registry.plugins
    )


def _probe_signal_translation() -> str:
    try:
        with graceful_signal_handlers():
            signal.raise_signal(signal.SIGTERM)
    except RuntimeCancelledError as error:
        return error.signal_name
    raise RuntimeContractError("SIGTERM did not enter the graceful cancellation path")


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
