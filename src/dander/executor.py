"""End-to-end execution for one project-defined Dander pipeline."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from dander.catalog import MetadataSpine, SemanticRegistryPublisher
from dander.runtime import PipelineRunResult
from dander.state import (
    LeaseHeartbeat,
    RunStage,
    RunStatus,
    classify_failure,
    mark_failure_diagnostic_logged,
)
from dander.telemetry import RunTelemetry
from dander.transform import SqlDialect, TransformProject, TransformRunResult
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from dander.catalog import CatalogAsset, CatalogPublisher, MetadataStore
    from dander.concurrency import OwnershipGuard
    from dander.ingestion import SourceConfig
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


class _IngestionRunner(Protocol):
    def run(
        self,
        *,
        run_id: str | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> PipelineRunResult:
        """Run configured endpoints and return a non-sensitive summary."""


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
    telemetry: RunTelemetry = field(default_factory=RunTelemetry)


class PipelineExecutor:
    """Own ingestion, transforms/tests, metadata publication, and lifecycle state."""

    def __init__(
        self,
        *,
        pipeline_id: str,
        source_config: SourceConfig,
        ingestion: _IngestionRunner,
        history: RunHistoryStore,
        catalog: str | None = None,
        project: str | None = None,
        raw_namespace: str | None = None,
        source_relations: dict[str, RelationRef] | None = None,
        models_dir: Path,
        selected_models: Iterable[str] | None,
        build_models: bool,
        transform_runner: _TransformRunner | None = None,
        metadata_store: MetadataStore | None = None,
        registry_output: Path | None = None,
        catalog_publisher: CatalogPublisher | None = None,
        dataplex_publisher: CatalogPublisher | None = None,
        leases: LeaseStore | None = None,
    ) -> None:
        if build_models and transform_runner is None:
            raise ValueError("A transform runner is required when build_models is enabled")
        if catalog is None:
            catalog = project
        elif project is not None and project != catalog:
            raise ValueError("catalog and legacy project must match")
        if catalog_publisher is not None and dataplex_publisher is not None:
            raise ValueError(
                "catalog_publisher and legacy dataplex_publisher are mutually exclusive"
            )
        configured_endpoints = {endpoint.name for endpoint in source_config.endpoints}
        if source_relations is not None:
            if missing := sorted(configured_endpoints - source_relations.keys()):
                raise ValueError(f"Missing source relation for endpoint: {missing[0]!r}")
            if unknown := sorted(source_relations.keys() - configured_endpoints):
                raise ValueError(f"Unknown source relation endpoint: {unknown[0]!r}")
            if not source_relations:
                raise ValueError("PipelineExecutor requires at least one source relation")
            canonical = next(iter(source_relations.values()))
            if any(
                (relation.catalog, relation.namespace) != (canonical.catalog, canonical.namespace)
                for relation in source_relations.values()
            ):
                raise ValueError(
                    "Pipeline source relations must share one catalog and raw namespace"
                )
            if catalog is not None and catalog != canonical.catalog:
                raise ValueError("catalog conflicts with canonical source relations")
            if raw_namespace is not None and raw_namespace != canonical.namespace:
                raise ValueError("raw_namespace conflicts with canonical source relations")
            catalog = canonical.catalog
            raw_namespace = canonical.namespace
            resolved_source_relations = dict(source_relations)
        else:
            if catalog is None:
                raise ValueError("PipelineExecutor requires a warehouse catalog")
            raw_namespace = raw_namespace or "raw"
            resolved_source_relations = {
                endpoint.name: RelationRef(
                    catalog=catalog,
                    namespace=raw_namespace,
                    name=f"{source_config.name}_{endpoint.name}",
                )
                for endpoint in source_config.endpoints
            }
        assert catalog is not None
        assert raw_namespace is not None
        self._pipeline_id = pipeline_id
        self._source_config = source_config
        self._ingestion = ingestion
        self._history = history
        self._catalog = catalog
        self._raw_namespace = raw_namespace
        self._source_relations = resolved_source_relations
        self._models_dir = models_dir
        self._selected_models = tuple(selected_models) if selected_models is not None else None
        self._build_models = build_models
        self._transform_runner = transform_runner
        self._metadata_store = metadata_store
        self._registry_output = registry_output
        self._catalog_publisher = catalog_publisher or dataplex_publisher
        self._leases = leases

    def execute(
        self,
        *,
        run_id: str | None = None,
        retry: bool = False,
    ) -> PipelineExecutionResult:
        """Execute every enabled stage and record one truthful terminal outcome."""
        started_ns = time.monotonic_ns()
        run_id = run_id or uuid4().hex
        stage = RunStage.INGEST
        endpoints = extracted = affected = models = assertions = assets = 0
        start_history = self._history.restart_retryable if retry else self._history.start
        start_history(run_id, self._source_config.name, pipeline_id=self._pipeline_id)
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
                        telemetry=RunTelemetry(duration_ms=_elapsed_ms(started_ns)),
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
                spine = MetadataSpine()
                if self._selected_models == () and not self._build_models:
                    compiled_assets = ()
                else:
                    transform_project = TransformProject.load(
                        self._models_dir,
                        catalog=self._catalog,
                        raw_namespace=self._raw_namespace,
                        target_dialect=getattr(
                            self._transform_runner,
                            "target_dialect",
                            SqlDialect.BIGQUERY,
                        ),
                    )
                    compiled_assets = spine.compile(
                        transform_project,
                        selected=self._selected_models,
                    )
                manifest = spine.pipeline_manifest(
                    pipeline_id=self._pipeline_id,
                    source=self._source_config,
                    assets=compiled_assets,
                    source_relations=self._source_relations,
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
                if self._catalog_publisher is not None:
                    for asset in compiled_assets:
                        if heartbeat is not None:
                            heartbeat.verify()
                        self._catalog_publisher.publish(asset)

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
            diagnostic = {
                "event": "pipeline_failed",
                "pipeline_id": self._pipeline_id,
                **failure.diagnostic_payload(run_id=run_id, stage=stage.value),
                "duration_ms": _elapsed_ms(started_ns),
            }
            _LOGGER.warning(
                json.dumps(diagnostic, separators=(",", ":"), sort_keys=True),
                extra={"dander_event": "pipeline_failed", **diagnostic},
            )
            mark_failure_diagnostic_logged(error)
            raise
        finally:
            if heartbeat is not None:
                heartbeat.__exit__()
        telemetry = RunTelemetry(
            duration_ms=_elapsed_ms(started_ns),
            operations=ingestion_result.telemetry + transform_result.telemetry,
        )
        _LOGGER.info(
            "pipeline_completed",
            extra={
                "dander_event": "pipeline_completed",
                "pipeline_id": self._pipeline_id,
                "run_id": run_id,
                "duration_ms": telemetry.duration_ms,
            },
        )
        return PipelineExecutionResult(
            run_id=run_id,
            pipeline_id=self._pipeline_id,
            ingestion=ingestion_result,
            models=transform_result.models,
            assertions=transform_result.assertions,
            assets=len(compiled_assets),
            telemetry=telemetry,
        )

    @property
    def _publishes_metadata(self) -> bool:
        return any(
            value is not None
            for value in (
                self._metadata_store,
                self._registry_output,
                self._catalog_publisher,
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


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
