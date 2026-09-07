"""Typed Control startup selection and shared-resource lifecycle tests."""

from __future__ import annotations

import builtins
import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import dander.control.startup_factory as startup_factory
from dander.control.graph_store import InMemoryGraphStore
from dander.control.run_composition import ControlRunComposition, ControlRunCompositionError
from dander.control.startup_bindings import (
    PostgreSQLRunStoreStartupBinding,
    PostgreSQLScheduleSourceStartupBinding,
    S3RunStoreStartupBinding,
    SQSScheduleSourceStartupBinding,
    serialize_run_store_startup_binding,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from dander.control.orchestration import ExecutionPlan, RunStore, TriggerSpec
    from dander.control.run_lifecycle import ControlRunLifecycle, PlanRunSubmissionResolver


@dataclass
class _Backend:
    events: list[str]

    def close(self) -> None:
        self.events.append("backend-close")


@dataclass
class _Source:
    events: list[str]

    def start(self) -> None:
        self.events.append("source-start")

    def ready(self) -> bool:
        return True

    def close(self) -> None:
        self.events.append("source-close")


@dataclass
class _Lifecycle:
    events: list[str]
    source: _Source | None = None
    closed: bool = False

    def install_submission_source(self, source: _Source) -> None:
        self.events.append("source-install")
        self.source = source

    def start_reconciler(self) -> None:
        self.events.append("reconciler-start")
        if self.source is not None:
            self.source.start()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.source is not None:
            self.source.close()
        self.events.append("lifecycle-close")


@dataclass
class _Pool:
    events: list[str]
    close_count: int = 0

    def close(self) -> None:
        self.close_count += 1
        self.events.append("pool-close")


def _registered(backend: _Backend) -> SimpleNamespace:
    return SimpleNamespace(
        backends={"test": backend},
        fargate_account_ids=frozenset(),
        fargate_regions=frozenset(),
    )


def _composition(lifecycle: _Lifecycle) -> ControlRunComposition:
    resolver = SimpleNamespace(plans=object())
    return ControlRunComposition(
        lifecycle=cast("ControlRunLifecycle", lifecycle),
        resolver=cast("PlanRunSubmissionResolver", resolver),
    )


def _patch_factory_shell(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    lifecycle: _Lifecycle,
    triggers: bool = False,
) -> None:
    backend = _Backend(events)
    monkeypatch.setattr(
        startup_factory,
        "load_execution_plans",
        lambda _paths: (cast("ExecutionPlan", object()),),
    )
    monkeypatch.setattr(
        startup_factory,
        "load_trigger_specs",
        lambda _paths: (cast("TriggerSpec", object()),) if triggers else (),
    )
    monkeypatch.setattr(
        startup_factory,
        "_build_registered_execution_backends",
        lambda **_kwargs: _registered(backend),
    )
    monkeypatch.setattr(startup_factory, "_bigquery_input_size_estimators", lambda *_a, **_k: ())

    def compose(**kwargs: object) -> ControlRunComposition:
        assert kwargs["start_reconciler"] is False
        events.append("compose")
        return _composition(lifecycle)

    monkeypatch.setattr(startup_factory, "compose_run_control", compose)


def test_binding_file_parse_never_imports_postgresql_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = PostgreSQLRunStoreStartupBinding(
        connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
        schema_name="dander_control",
    )
    path = tmp_path / "run-store.json"
    path.write_bytes(serialize_run_store_startup_binding(binding))
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> object:
        if name.startswith("psycopg") or name.startswith("dander.control.postgresql_"):
            raise AssertionError("PostgreSQL runtime imported during config parsing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert startup_factory.load_run_store_startup_binding(path) == binding


def test_postgresql_resources_use_one_bounded_dict_pool_and_migrate_before_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    created: dict[str, object] = {}

    class Pool(_Pool):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(events)
            created.update(kwargs)
            events.append("pool-create")

        def open(self, *, wait: bool, timeout: float) -> None:
            created["open_wait"] = wait
            created["open_timeout"] = timeout
            events.append("pool-open")

    class Database:
        def __init__(self, *, pool: object, schema_name: str) -> None:
            self.pool = pool
            self.schema_name = schema_name
            events.append("database-create")

    class Migrator:
        def __init__(self, database: Database) -> None:
            self.database = database

        def migrate(self) -> int:
            events.append("migrate")
            return 3

    class GraphStore:
        def __init__(self, database: Database) -> None:
            self.database = database
            events.append("graph-store-create")

    class RunStore:
        def __init__(self, database: Database) -> None:
            self.database = database
            events.append("run-store-create")

    rows_module = ModuleType("psycopg.rows")
    rows_module.dict_row = object()  # type: ignore[attr-defined]
    pool_module = ModuleType("psycopg_pool")
    pool_module.ConnectionPool = Pool  # type: ignore[attr-defined]
    database_module = ModuleType("dander.control.postgresql_control_database")
    database_module.PostgreSQLControlDatabase = Database  # type: ignore[attr-defined]
    database_module.PostgreSQLControlMigrator = Migrator  # type: ignore[attr-defined]
    graph_module = ModuleType("dander.control.postgresql_graph_store")
    graph_module.PostgreSQLGraphStore = GraphStore  # type: ignore[attr-defined]
    run_module = ModuleType("dander.control.postgresql_run_store")
    run_module.PostgreSQLRunStore = RunStore  # type: ignore[attr-defined]
    for name, module in (
        ("psycopg.rows", rows_module),
        ("psycopg_pool", pool_module),
        ("dander.control.postgresql_control_database", database_module),
        ("dander.control.postgresql_graph_store", graph_module),
        ("dander.control.postgresql_run_store", run_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    resources = startup_factory._open_postgresql_resources(  # noqa: SLF001
        PostgreSQLRunStoreStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        ),
        environ={"DANDER_CONTROL_DATABASE_URL": "postgresql://secret-value"},
    )

    assert created["min_size"] == 1
    assert created["max_size"] == 8
    assert created["open"] is False
    assert created["open_wait"] is True
    assert created["kwargs"] == {"row_factory": rows_module.dict_row}
    assert resources.graph_store.database is resources.database  # type: ignore[attr-defined]
    assert resources.run_store.database is resources.database  # type: ignore[attr-defined]
    assert events == [
        "pool-create",
        "pool-open",
        "database-create",
        "migrate",
        "graph-store-create",
        "run-store-create",
    ]


def test_postgresql_startup_installs_schedule_before_reconciler_and_closes_pool_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = _Lifecycle(events)
    pool = _Pool(events)
    graph_store = InMemoryGraphStore()
    run_store = cast("RunStore", object())
    database = SimpleNamespace(schema_name="dander_control")
    resources = SimpleNamespace(
        database=database,
        pool=pool,
        graph_store=graph_store,
        run_store=run_store,
        connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
    )
    _patch_factory_shell(monkeypatch, events=events, lifecycle=lifecycle, triggers=True)

    def open_resources(*_args: object, **_kwargs: object) -> object:
        events.append("migrate")
        return resources

    source = _Source(events)
    monkeypatch.setattr(startup_factory, "_open_postgresql_resources", open_resources)
    monkeypatch.setattr(
        startup_factory,
        "_build_schedule_submission_source",
        lambda **_kwargs: source,
    )
    startup = startup_factory.build_control_startup(
        graph_store=None,
        run_store_binding=PostgreSQLRunStoreStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        ),
        schedule_source_binding=PostgreSQLScheduleSourceStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        ),
        project_config=cast("Path", object()),
        plan_paths=(cast("Path", object()),),
        trigger_paths=(cast("Path", object()),),
        environment="onprem",
        environ={"DANDER_CONTROL_DATABASE_URL": "unused-by-test"},
    )

    assert startup.graph_store is graph_store
    assert events == [
        "migrate",
        "compose",
        "source-install",
        "reconciler-start",
        "source-start",
    ]
    startup.close()
    startup.close()
    assert events[-3:] == ["source-close", "lifecycle-close", "pool-close"]
    assert pool.close_count == 1


def test_postgresql_schedule_install_failure_closes_lifecycle_and_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = _Lifecycle(events)
    pool = _Pool(events)
    resources = SimpleNamespace(
        database=SimpleNamespace(schema_name="dander_control"),
        pool=pool,
        graph_store=InMemoryGraphStore(),
        run_store=cast("RunStore", object()),
        connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
    )
    _patch_factory_shell(monkeypatch, events=events, lifecycle=lifecycle, triggers=True)
    monkeypatch.setattr(
        startup_factory,
        "_open_postgresql_resources",
        lambda *_a, **_k: resources,
    )
    monkeypatch.setattr(
        startup_factory,
        "_build_schedule_submission_source",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("simulated install failure")),
    )

    with pytest.raises(ControlRunCompositionError, match="typed Control startup binding"):
        startup_factory.build_control_startup(
            graph_store=None,
            run_store_binding=PostgreSQLRunStoreStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="dander_control",
            ),
            schedule_source_binding=PostgreSQLScheduleSourceStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="dander_control",
            ),
            project_config=cast("Path", object()),
            plan_paths=(cast("Path", object()),),
            trigger_paths=(cast("Path", object()),),
            environment="onprem",
        )

    assert events[-2:] == ["lifecycle-close", "pool-close"]
    assert pool.close_count == 1


def test_s3_typed_arm_preserves_current_constructor_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = _Lifecycle(events)
    calls: list[tuple[str, dict[str, object]]] = []
    graph_store = InMemoryGraphStore()
    _patch_factory_shell(monkeypatch, events=events, lifecycle=lifecycle)

    def make_store(bucket: str, **kwargs: object) -> object:
        calls.append((bucket, kwargs))
        return cast("RunStore", object())

    monkeypatch.setattr(startup_factory, "S3RunStore", make_store)
    startup = startup_factory.build_control_startup(
        graph_store=graph_store,
        run_store_binding=S3RunStoreStartupBinding(
            bucket="dander-control-runs",
            prefix="control/runs/v1",
            expected_bucket_owner="123456789012",
            region="us-east-1",
        ),
        schedule_source_binding=None,
        project_config=cast("Path", object()),
        plan_paths=(cast("Path", object()),),
        environment="production",
    )

    assert calls == [
        (
            "dander-control-runs",
            {
                "prefix": "control/runs/v1",
                "expected_bucket_owner": "123456789012",
            },
        )
    ]
    startup.close()


def test_sqs_typed_arm_must_match_registered_fargate_account_and_region() -> None:
    run_binding = S3RunStoreStartupBinding(
        bucket="dander-control-runs",
        prefix="control/runs/v1",
        expected_bucket_owner="123456789012",
        region="us-east-1",
    )
    schedule_binding = SQSScheduleSourceStartupBinding(
        queue_url="https://sqs.us-west-2.amazonaws.com/123456789012/dander-wakeups",
        expected_account_id="123456789012",
        region="us-west-2",
    )

    with pytest.raises(ControlRunCompositionError, match="same AWS account and region"):
        startup_factory._validate_binding_compatibility(  # noqa: SLF001
            run_store_binding=run_binding,
            schedule_source_binding=schedule_binding,
            fargate_account_ids=frozenset({"123456789012"}),
            fargate_regions=frozenset({"us-east-1"}),
        )


def test_postgresql_initialization_sanitizes_dsn_bearing_driver_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg_pool

    secret = "postgresql://operator:super-secret@database/control"

    class BrokenPool:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError(f"connection failed for {secret}")

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", BrokenPool)

    with pytest.raises(ControlRunCompositionError) as captured:
        startup_factory._open_postgresql_resources(  # noqa: SLF001
            PostgreSQLRunStoreStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="dander_control",
            ),
            environ={"DANDER_CONTROL_DATABASE_URL": secret},
        )

    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
