"""Project-level executor lifecycle tests."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from dander.catalog import MetadataSnapshot, MetadataStore
from dander.executor import PipelineExecutor
from dander.ingestion import Endpoint, SourceConfig
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.state import (
    LeaseHandle,
    LeaseLostError,
    LeaseStore,
    RunHistoryStore,
    RunStage,
    RunStatus,
)
from dander.transform import TransformRunResult
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from dander.catalog import CatalogAsset, CatalogPublisher
    from dander.concurrency import OwnershipGuard


class _Ingestion:
    def run(
        self,
        *,
        run_id: str | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> PipelineRunResult:
        assert run_id is not None
        return PipelineRunResult(
            run_id=run_id,
            source="example",
            endpoints=(
                EndpointRunResult(
                    endpoint="widgets",
                    extracted=3,
                    affected=2,
                    committed_cursor="2026-01-01T00:00:00Z",
                ),
            ),
        )


class _Transform:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        assert models_dir.is_dir()
        assert tuple(selected or ()) == ("stg_widgets",)
        if self._fail:
            raise RuntimeError("transform failed")
        return TransformRunResult(models=("stg_widgets",), assertions=2)


class _History(RunHistoryStore):
    def __init__(self) -> None:
        self.started: tuple[str, str, str] | None = None
        self.checkpoints: list[RunStage] = []
        self.finished: tuple[RunStatus, RunStage | None, int, int, int] | None = None
        self.failure: tuple[str | None, str | None] | None = None
        self.reconciled: tuple[str, str] | None = None

    def start(self, run_id: str, source: str, *, pipeline_id: str | None = None) -> None:
        assert pipeline_id is not None
        self.started = (run_id, source, pipeline_id)

    def checkpoint(
        self,
        run_id: str,
        stage: RunStage,
        **kwargs: int,
    ) -> None:
        assert self.started is not None and run_id == self.started[0]
        self.checkpoints.append(stage)

    def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        endpoints: int,
        extracted: int,
        affected: int,
        models: int = 0,
        assertions: int = 0,
        assets: int = 0,
        failure_stage: RunStage | None = None,
        failure_code: str | None = None,
        failure_summary: str | None = None,
    ) -> None:
        assert self.started is not None and run_id == self.started[0]
        self.finished = (status, failure_stage, models, assertions, assets)
        self.failure = (failure_code, failure_summary)

    def reconcile_interrupted(self, pipeline_id: str, *, current_run_id: str) -> None:
        self.reconciled = (pipeline_id, current_run_id)


class _Metadata(MetadataStore):
    def __init__(self) -> None:
        self.manifest: dict[str, object] | None = None

    def publish(
        self,
        *,
        pipeline_id: str,
        run_id: str,
        manifest: dict[str, object],
    ) -> None:
        assert pipeline_id == "example_pipeline"
        assert run_id
        self.manifest = manifest

    def snapshots(self, *, pipeline_id: str | None = None) -> tuple[MetadataSnapshot, ...]:
        return ()


class _CatalogPublisher:
    def __init__(self) -> None:
        self.assets: list[CatalogAsset] = []

    def publish(self, asset: CatalogAsset) -> str:
        self.assets.append(asset)
        return f"catalog://{asset.relation}"


class _Leases(LeaseStore):
    def __init__(self, *, available: bool, heartbeat: bool = True) -> None:
        self._available = available
        self._heartbeat = heartbeat
        self.released = False

    @property
    def lease_seconds(self) -> int:
        return 30

    def acquire(self, pipeline_id: str, run_id: str) -> LeaseHandle | None:
        if not self._available:
            return None
        return LeaseHandle(pipeline_id, run_id, 1, self.lease_seconds)

    def heartbeat(self, lease: LeaseHandle) -> bool:
        return self._heartbeat

    def release(self, lease: LeaseHandle) -> bool:
        self.released = True
        return True


def _models(models_dir: Path) -> None:
    staging = models_dir / "staging"
    staging.mkdir(parents=True)
    (staging / "stg_widgets.sql").write_text(
        "SELECT id FROM {{ ref('raw_example_widgets') }}",
        encoding="utf-8",
    )
    (staging / "stg_widgets.yml").write_text(
        """model: stg_widgets
description: Governed widgets.
owner: data-eng
source_system: example
sensitivity: internal
columns:
  - name: id
    type: STRING
    description: Widget identifier.
tests:
  - column: id
    not_null: true
    unique: true
metrics:
  - name: widget_count
    description: Number of distinct widgets.
    aggregation: count_distinct
    field: id
""",
        encoding="utf-8",
    )


def _executor(
    models_dir: Path,
    *,
    history: _History,
    metadata: _Metadata,
    fail_transform: bool = False,
    leases: LeaseStore | None = None,
    catalog: str | None = None,
    project: str | None = "valid-project-123",
    raw_namespace: str | None = "raw",
    source_relations: dict[str, RelationRef] | None = None,
    catalog_publisher: CatalogPublisher | None = None,
) -> PipelineExecutor:
    source = SourceConfig(
        name="example",
        base_url="https://example.test",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="widgets",
                path="/widgets",
                primary_key=["id"],
                incremental_cursor="updated_at",
            )
        ],
    )
    return PipelineExecutor(
        pipeline_id="example_pipeline",
        source_config=source,
        ingestion=_Ingestion(),
        history=history,
        catalog=catalog,
        project=project,
        raw_namespace=raw_namespace,
        source_relations=source_relations,
        models_dir=models_dir,
        selected_models=("stg_widgets",),
        build_models=True,
        transform_runner=_Transform(fail=fail_transform),
        metadata_store=metadata,
        catalog_publisher=catalog_publisher,
        leases=leases,
    )


def test_executor_records_one_truthful_complete_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _models(tmp_path)
    history = _History()
    metadata = _Metadata()
    clock = iter((1_000_000, 8_500_000))
    monkeypatch.setattr("dander.executor.time.monotonic_ns", lambda: next(clock))

    result = _executor(tmp_path, history=history, metadata=metadata).execute()

    assert history.started is not None
    assert history.checkpoints == [RunStage.TRANSFORM, RunStage.METADATA]
    assert history.finished == (RunStatus.SUCCEEDED, None, 1, 2, 1)
    assert result.ingestion.run_id == result.run_id
    assert result.assets == 1
    assert result.telemetry.duration_ms == 7
    assert metadata.manifest is not None
    assert metadata.manifest["pipeline_id"] == "example_pipeline"
    assets = metadata.manifest["assets"]
    assert isinstance(assets, list)
    assert assets[0]["metrics"][0]["calculation"] == "COUNT(DISTINCT `id`)"


def test_executor_preserves_launcher_supplied_run_id(tmp_path: Path) -> None:
    _models(tmp_path)
    history = _History()

    result = _executor(tmp_path, history=history, metadata=_Metadata()).execute(
        run_id="launcher-run-42"
    )

    assert result.run_id == "launcher-run-42"
    assert history.started == ("launcher-run-42", "example", "example_pipeline")


def test_executor_publishes_through_provider_neutral_catalog_boundary(tmp_path: Path) -> None:
    _models(tmp_path)
    publisher = _CatalogPublisher()

    result = _executor(
        tmp_path,
        history=_History(),
        metadata=_Metadata(),
        catalog_publisher=publisher,
    ).execute()

    assert result.assets == 1
    assert [asset.relation for asset in publisher.assets] == [
        "valid-project-123.staging.stg_widgets"
    ]


def test_executor_keeps_custom_raw_namespace_in_models_and_metadata(tmp_path: Path) -> None:
    _models(tmp_path)
    metadata = _Metadata()
    source_relation = RelationRef(
        catalog="valid-project-123",
        namespace="landing",
        name="example_widgets",
    )

    _executor(
        tmp_path,
        history=_History(),
        metadata=metadata,
        catalog=None,
        project=None,
        raw_namespace=None,
        source_relations={"widgets": source_relation},
    ).execute()

    assert metadata.manifest is not None
    source = metadata.manifest["source"]
    assert isinstance(source, dict)
    endpoints = source["endpoints"]
    assert isinstance(endpoints, list)
    assert endpoints[0]["relation"] == "valid-project-123.landing.example_widgets"
    assets = metadata.manifest["assets"]
    assert isinstance(assets, list)
    assert assets[0]["upstream_relations"] == ["valid-project-123.landing.example_widgets"]


@pytest.mark.parametrize(
    ("catalog", "raw_namespace", "message"),
    [
        ("other-catalog", None, "catalog conflicts with canonical source relations"),
        (None, "other_namespace", "raw_namespace conflicts with canonical source relations"),
    ],
)
def test_executor_rejects_legacy_coordinates_that_conflict_with_source_relations(
    tmp_path: Path,
    *,
    catalog: str | None,
    raw_namespace: str | None,
    message: str,
) -> None:
    _models(tmp_path)
    source_relation = RelationRef(
        catalog="canonical_catalog",
        namespace="canonical_namespace",
        name="example_widgets",
    )

    with pytest.raises(ValueError, match=message):
        _executor(
            tmp_path,
            history=_History(),
            metadata=_Metadata(),
            catalog=catalog,
            project=None,
            raw_namespace=raw_namespace,
            source_relations={"widgets": source_relation},
        )


@pytest.mark.parametrize(
    ("source_relations", "message"),
    [
        (
            {
                "widgets": RelationRef(
                    catalog="warehouse",
                    namespace="landing",
                    name="example_widgets",
                )
            },
            "Missing source relation for endpoint: 'gadgets'",
        ),
        (
            {
                "widgets": RelationRef(
                    catalog="warehouse",
                    namespace="landing",
                    name="example_widgets",
                ),
                "gadgets": RelationRef(
                    catalog="warehouse",
                    namespace="other_namespace",
                    name="example_gadgets",
                ),
            },
            "must share one catalog and raw namespace",
        ),
    ],
)
def test_executor_requires_one_complete_canonical_source_location(
    tmp_path: Path,
    *,
    source_relations: dict[str, RelationRef],
    message: str,
) -> None:
    source = SourceConfig(
        name="example",
        base_url="https://example.test",
        auth_strategy="none",
        endpoints=[
            Endpoint(name="widgets", path="/widgets", primary_key=["id"]),
            Endpoint(name="gadgets", path="/gadgets", primary_key=["id"]),
        ],
    )

    with pytest.raises(ValueError, match=message):
        PipelineExecutor(
            pipeline_id="example_pipeline",
            source_config=source,
            ingestion=_Ingestion(),
            history=_History(),
            source_relations=source_relations,
            models_dir=tmp_path,
            selected_models=None,
            build_models=False,
        )


def test_executor_marks_transform_failure_without_claiming_ingestion_only_success(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _models(tmp_path)
    history = _History()
    caplog.set_level(logging.WARNING, logger="dander.executor")

    with pytest.raises(RuntimeError, match="transform failed"):
        _executor(
            tmp_path,
            history=history,
            metadata=_Metadata(),
            fail_transform=True,
        ).execute()

    assert history.checkpoints == [RunStage.TRANSFORM]
    assert history.finished == (RunStatus.FAILED, RunStage.TRANSFORM, 0, 0, 0)
    assert history.failure is not None
    assert history.failure[0] == "transform_failed"
    assert "Inspect logs for run" in (history.failure[1] or "")
    terminal = next(record for record in caplog.records if record.msg == "pipeline_failed")
    assert terminal.__dict__["dander_event"] == "pipeline_failed"
    assert terminal.__dict__["pipeline_id"] == "example_pipeline"
    assert terminal.__dict__["failure_code"] == "transform_failed"
    assert not hasattr(terminal, "exception")


def test_executor_records_active_overlap_as_skipped_without_running_pipeline(
    tmp_path: Path,
) -> None:
    _models(tmp_path)
    history = _History()

    result = _executor(
        tmp_path,
        history=history,
        metadata=_Metadata(),
        leases=_Leases(available=False),
    ).execute()

    assert result.skipped
    assert result.ingestion.endpoints == ()
    assert history.checkpoints == []
    assert history.finished == (RunStatus.SKIPPED, None, 0, 0, 0)
    assert history.reconciled is None


def test_executor_fails_closed_before_transform_when_heartbeat_is_lost(
    tmp_path: Path,
) -> None:
    _models(tmp_path)
    history = _History()
    leases = _Leases(available=True, heartbeat=False)

    with pytest.raises(LeaseLostError, match="ownership was lost"):
        _executor(
            tmp_path,
            history=history,
            metadata=_Metadata(),
            leases=leases,
        ).execute()

    assert history.checkpoints == [RunStage.TRANSFORM]
    assert history.finished == (RunStatus.FAILED, RunStage.TRANSFORM, 0, 0, 0)
    assert history.failure is not None and history.failure[0] == "lease_failed"
    assert history.reconciled is not None
    assert leases.released
