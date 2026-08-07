"""End-to-end execution for one project-defined Dander pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from dander.catalog import MetadataSpine, SemanticRegistryPublisher
from dander.runtime import PipelineRunResult
from dander.state import LeaseHeartbeat, RunStage, RunStatus, classify_failure
from dander.transform import TransformProject, TransformRunResult

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from dander.catalog import CatalogAsset, MetadataStore
    from dander.concurrency import OwnershipGuard
    from dander.ingestion import SourceConfig
    from dander.runtime import PipelineRunner
    from dander.state import LeaseStore, RunHistoryStore

_LOGGER = logging.getLogger(__name__)


class _TransformRunner(Protocol):
    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Build models and run their assertions."""


class _DataplexPublisher(Protocol):
    def publish(self, asset: CatalogAsset) -> str:
        """Project one canonical asset into Dataplex."""


@dataclass(frozen=True)
class PipelineExecutionResult:
    """Non-sensitive summary for the complete Dander pipeline lifecycle."""

    run_id: str
    pipeline_id: str
    ingestion: PipelineRunResult
    models: tuple[str, ...]
    assertions: int
    assets: int
    skipped: bool = False


class PipelineExecutor:
    """Own ingestion, transforms/tests, metadata publication, and lifecycle state."""

    def __init__(
        self,
        *,
        pipeline_id: str,
        source_config: SourceConfig,
        ingestion: PipelineRunner,
        history: RunHistoryStore,
        project: str,
        models_dir: Path,
        selected_models: Iterable[str] | None,
        build_models: bool,
        transform_runner: _TransformRunner | None = None,
        metadata_store: MetadataStore | None = None,
        registry_output: Path | None = None,
        dataplex_publisher: _DataplexPublisher | None = None,
        leases: LeaseStore | None = None,
    ) -> None:
        if build_models and transform_runner is None:
            raise ValueError("A transform runner is required when build_models is enabled")
        self._pipeline_id = pipeline_id
        self._source_config = source_config
        self._ingestion = ingestion
        self._history = history
        self._project = project
        self._models_dir = models_dir
        self._selected_models = tuple(selected_models) if selected_models is not None else None
        self._build_models = build_models
        self._transform_runner = transform_runner
        self._metadata_store = metadata_store
        self._registry_output = registry_output
        self._dataplex_publisher = dataplex_publisher
        self._leases = leases

    def execute(self, *, run_id: str | None = None) -> PipelineExecutionResult:
        """Execute every enabled stage and record one truthful terminal outcome."""
        run_id = run_id or uuid4().hex
        stage = RunStage.INGEST
        endpoints = extracted = affected = models = assertions = assets = 0
        self._history.start(
            run_id,
            self._source_config.name,
            pipeline_id=self._pipeline_id,
        )
        heartbeat: LeaseHeartbeat | None = None
        try:
            if self._leases is not None:
                lease = self._leases.acquire(self._pipeline_id, run_id)
                if lease is None:
                    self._history.finish(
                        run_id,
                        RunStatus.SKIPPED,
                        endpoints=0,
                        extracted=0,
                        affected=0,
                    )
                    _LOGGER.info(
                        "pipeline_overlap_skipped",
                        extra={
                            "dander_event": "pipeline_overlap_skipped",
                            "pipeline_id": self._pipeline_id,
                            "run_id": run_id,
                        },
                    )
                    return PipelineExecutionResult(
                        run_id=run_id,
                        pipeline_id=self._pipeline_id,
                        ingestion=PipelineRunResult(
                            run_id=run_id,
                            source=self._source_config.name,
                            endpoints=(),
                        ),
                        models=(),
                        assertions=0,
                        assets=0,
                        skipped=True,
                    )
                heartbeat = LeaseHeartbeat(self._leases, lease)
                heartbeat.__enter__()
                self._history.reconcile_interrupted(
                    self._pipeline_id,
                    current_run_id=run_id,
                )
            ingestion_result = self._ingestion.run(
                run_id=run_id,
                ownership=heartbeat,
            )
            endpoints = len(ingestion_result.endpoints)
            extracted = sum(result.extracted for result in ingestion_result.endpoints)
            affected = sum(result.affected for result in ingestion_result.endpoints)

            transform_result = TransformRunResult(models=(), assertions=0)
            if self._build_models:
                stage = RunStage.TRANSFORM
                self._checkpoint(
                    run_id,
                    stage,
                    endpoints=endpoints,
                    extracted=extracted,
                    affected=affected,
                )
                assert self._transform_runner is not None
                if heartbeat is not None:
                    heartbeat.verify()
                transform_result = self._transform_runner.build(
                    self._models_dir,
                    selected=self._selected_models,
                    ownership=heartbeat,
                )
                models = len(transform_result.models)
                assertions = transform_result.assertions

            compiled_assets: tuple[CatalogAsset, ...] = ()
            if self._publishes_metadata:
                stage = RunStage.METADATA
                self._checkpoint(
                    run_id,
                    stage,
                    endpoints=endpoints,
                    extracted=extracted,
                    affected=affected,
                    models=models,
                    assertions=assertions,
                )
                transform_project = TransformProject.load(
                    self._models_dir,
                    project_id=self._project,
                )
                spine = MetadataSpine()
                compiled_assets = spine.compile(
                    transform_project,
                    selected=self._selected_models,
                )
                manifest = spine.pipeline_manifest(
                    pipeline_id=self._pipeline_id,
                    source=self._source_config,
                    assets=compiled_assets,
                )
                assets = len(compiled_assets)
                if self._metadata_store is not None:
                    if heartbeat is not None:
                        heartbeat.verify()
                    self._metadata_store.publish(
                        pipeline_id=self._pipeline_id,
                        run_id=run_id,
                        manifest=manifest,
                    )
                if self._registry_output is not None:
                    if heartbeat is not None:
                        heartbeat.verify()
                    SemanticRegistryPublisher().publish(manifest, self._registry_output)
                if self._dataplex_publisher is not None:
                    for asset in compiled_assets:
                        if heartbeat is not None:
                            heartbeat.verify()
                        self._dataplex_publisher.publish(asset)

            if heartbeat is not None:
                heartbeat.verify()
            self._history.finish(
                run_id,
                RunStatus.SUCCEEDED,
                endpoints=endpoints,
                extracted=extracted,
                affected=affected,
                models=models,
                assertions=assertions,
                assets=assets,
            )
        except Exception as error:
            failure = classify_failure(error, stage=stage, run_id=run_id)
            try:
                self._history.finish(
                    run_id,
                    RunStatus.FAILED,
                    endpoints=endpoints,
                    extracted=extracted,
                    affected=affected,
                    models=models,
                    assertions=assertions,
                    assets=assets,
                    failure_stage=stage,
                    failure_code=failure.code,
                    failure_summary=failure.summary,
                )
            except Exception:
                _LOGGER.exception(
                    "run_history_finish_failed",
                    extra={
                        "dander_event": "run_history_finish_failed",
                        "pipeline_id": self._pipeline_id,
                        "run_id": run_id,
                    },
                )
            raise
        finally:
            if heartbeat is not None:
                heartbeat.__exit__()
        return PipelineExecutionResult(
            run_id=run_id,
            pipeline_id=self._pipeline_id,
            ingestion=ingestion_result,
            models=transform_result.models,
            assertions=transform_result.assertions,
            assets=len(compiled_assets),
        )

    @property
    def _publishes_metadata(self) -> bool:
        return any(
            value is not None
            for value in (
                self._metadata_store,
                self._registry_output,
                self._dataplex_publisher,
            )
        )

    def _checkpoint(
        self,
        run_id: str,
        stage: RunStage,
        *,
        endpoints: int,
        extracted: int,
        affected: int,
        models: int = 0,
        assertions: int = 0,
    ) -> None:
        self._history.checkpoint(
            run_id,
            stage,
            endpoints=endpoints,
            extracted=extracted,
            affected=affected,
            models=models,
            assertions=assertions,
        )
