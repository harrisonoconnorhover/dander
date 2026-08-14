"""Provider-free runtime inspection and local lifecycle conformance."""

from __future__ import annotations

import json
import os
import re
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
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

_REVISION = re.compile(r"^(?:unknown|[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    """Non-secret installed runtime and plugin metadata."""

    contract: str
    capability_manifest: str
    build: Mapping[str, object]
    adapters: Mapping[str, object]
    plugins: tuple[Mapping[str, object], ...]

    def to_json(self) -> str:
        """Render deterministic inspection JSON for humans and launchers."""
        return json.dumps(
            {
                "contract": self.contract,
                "capability_manifest": self.capability_manifest,
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


def inspect_runtime(
    config: Path,
    *,
    platforms_path: Path | None = None,
    deployment: str | None = None,
) -> RuntimeInspection:
    """Inspect configured packages without constructing sources or contacting providers."""
    manifest = load_project_config(
        config,
        platforms_path=platforms_path,
        deployment=deployment,
    )
    registry = load_connector_plugins(manifest.plugins)
    capabilities = _load_capabilities()
    capability_adapters = capabilities["adapters"]
    if not isinstance(capability_adapters, dict):
        raise RuntimeContractError("runtime capability manifest is incompatible")
    return RuntimeInspection(
        contract=RUNTIME_CONTRACT,
        capability_manifest=str(capabilities["schema"]),
        build=_build_metadata(),
        adapters={
            **capability_adapters,
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


def _build_metadata() -> dict[str, object]:
    build: dict[str, object] = {
        "distribution": "dander-platform",
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    revision = os.environ.get("DANDER_BUILD_REVISION")
    if revision is not None:
        if not _REVISION.fullmatch(revision):
            raise RuntimeContractError("DANDER_BUILD_REVISION is not a valid OCI revision")
        build["revision"] = revision
    created = os.environ.get("DANDER_BUILD_CREATED")
    if created is not None:
        try:
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimeContractError(
                "DANDER_BUILD_CREATED is not an ISO-8601 timestamp"
            ) from error
        if parsed.tzinfo is None:
            raise RuntimeContractError("DANDER_BUILD_CREATED must include a timezone")
        build["created"] = created
    digest = os.environ.get("DANDER_IMAGE_DIGEST")
    if digest is not None:
        if not _DIGEST.fullmatch(digest):
            raise RuntimeContractError("DANDER_IMAGE_DIGEST is not a valid sha256 digest")
        build["image_digest"] = digest
    return build


def _load_capabilities() -> Mapping[str, object]:
    try:
        raw = (
            resources.files("dander")
            .joinpath("runtime-capabilities.json")
            .read_text(encoding="utf-8")
        )
        capabilities = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeContractError("runtime capability manifest is unavailable") from error
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("schema") != "io.dander.runtime.capabilities/v1"
        or capabilities.get("contract") != RUNTIME_CONTRACT
        or not isinstance(capabilities.get("adapters"), dict)
    ):
        raise RuntimeContractError("runtime capability manifest is incompatible")
    return capabilities


def _probe_signal_translation() -> str:
    try:
        with graceful_signal_handlers():
            signal.raise_signal(signal.SIGTERM)
    except RuntimeCancelledError as error:
        return error.signal_name
    raise RuntimeContractError("SIGTERM did not enter the graceful cancellation path")


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
