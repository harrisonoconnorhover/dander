"""Typed hosted-Control startup with optional PostgreSQL durability.

Startup binding files contain resource coordinates only.  The PostgreSQL driver, DSN, pool, and
adapters are reached only after the PostgreSQL union arm has been selected.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

from dander.control.application import ControlOperationError
from dander.control.orchestration import (
    ExecutionBackendError,
    OrchestrationContractError,
    PlacementCandidate,
    RunStore,
    RunStoreError,
    SizeClassCandidate,
)
from dander.control.run_composition import (
    ControlRunComposition,
    ControlRunCompositionError,
    _bigquery_input_size_estimators,
    _build_registered_execution_backends,
    compose_run_control,
    load_execution_plans,
    load_trigger_specs,
)
from dander.control.s3_run_store import S3RunStore
from dander.control.schedule_consumer import (
    ControlScheduleConsumer,
    ScheduledRunSubmissionResolver,
    ScheduleQueueError,
)
from dander.control.sqs_schedule_queue import SQSScheduleQueue
from dander.control.startup_bindings import (
    PostgreSQLRunStoreStartupBinding,
    PostgreSQLScheduleSourceStartupBinding,
    RunStoreStartupBinding,
    S3RunStoreStartupBinding,
    ScheduleSourceStartupBinding,
    SQSScheduleSourceStartupBinding,
    deserialize_run_store_startup_binding,
    deserialize_schedule_source_startup_binding,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from dander.control.graph_store import GraphStore
    from dander.control.orchestration import TriggerSpec
    from dander.control.postgresql_control_database import (
        PostgreSQLControlDatabase,
        PostgreSQLControlPool,
    )
    from dander.control.run_lifecycle import RunSubmissionSource

_MAX_STARTUP_BINDING_BYTES = 16 * 1024
_POSTGRESQL_POOL_MIN_SIZE = 1
_POSTGRESQL_POOL_MAX_SIZE = 8
_POSTGRESQL_CONNECT_TIMEOUT_SECONDS = 5.0


class _Closeable(Protocol):
    def close(self) -> object: ...


@dataclass(slots=True)
class ControlStartup:
    """Own the selected graph store, run composition, and optional shared pool."""

    graph_store: GraphStore
    composition: ControlRunComposition
    _shared_pool: _Closeable | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        """Close sources and lifecycle before closing the shared pool exactly once."""
        if self._closed:
            return
        self._closed = True
        failure: Exception | None = None
        try:
            self.composition.lifecycle.close()
        except Exception as error:  # noqa: BLE001 - the pool must still close
            failure = error
        pool, self._shared_pool = self._shared_pool, None
        if pool is not None:
            try:
                pool.close()
            except Exception as error:  # noqa: BLE001 - sanitize driver failures
                if failure is None:
                    failure = error
        if failure is not None:
            raise ControlRunCompositionError(
                "The typed Control startup could not close cleanly."
            ) from None


@dataclass(frozen=True, slots=True)
class _PostgreSQLResources:
    database: PostgreSQLControlDatabase
    pool: PostgreSQLControlPool
    graph_store: GraphStore
    run_store: RunStore
    connection_environment_variable: str


def load_run_store_startup_binding(path: Path) -> RunStoreStartupBinding:
    """Read one bounded canonical run-store startup binding without adapter access."""
    return deserialize_run_store_startup_binding(_read_startup_binding(path))


def load_schedule_source_startup_binding(path: Path) -> ScheduleSourceStartupBinding:
    """Read one bounded canonical schedule-source startup binding without adapter access."""
    return deserialize_schedule_source_startup_binding(_read_startup_binding(path))


def build_control_startup(
    *,
    graph_store: GraphStore | None,
    run_store_binding: RunStoreStartupBinding,
    schedule_source_binding: ScheduleSourceStartupBinding | None,
    project_config: Path,
    platforms_config: Path | None = None,
    plan_paths: Sequence[Path],
    environment: str,
    placement_candidates: Iterable[PlacementCandidate] = (),
    preferred_locality: str | None = None,
    max_cost_microusd: int | None = None,
    size_class_candidates: Iterable[SizeClassCandidate] = (),
    default_size_class: str | None = None,
    deployment_name: str = "dander",
    gcp_project_id: str | None = None,
    gcp_deployment_name: str = "gcp_cloud_run",
    reconcile_interval_seconds: float = 5.0,
    shutdown_grace_seconds: float = 35.0,
    trigger_paths: Sequence[Path] = (),
    environ: Mapping[str, str] | None = None,
) -> ControlStartup:
    """Build one typed Control profile while keeping provider-neutral composition unchanged."""
    if bool(trigger_paths) != (schedule_source_binding is not None):
        raise ControlRunCompositionError(
            "Control trigger specs and schedule-source binding must be configured together."
        )
    plans = load_execution_plans(plan_paths)
    triggers = load_trigger_specs(trigger_paths) if trigger_paths else ()
    sizes = tuple(size_class_candidates)
    registered = _build_registered_execution_backends(
        plans=plans,
        project_config=project_config,
        platforms_config=platforms_config,
        deployment_name=deployment_name,
        gcp_project_id=gcp_project_id,
        gcp_deployment_name=gcp_deployment_name,
    )
    try:
        _validate_binding_compatibility(
            run_store_binding=run_store_binding,
            schedule_source_binding=schedule_source_binding,
            fargate_account_ids=registered.fargate_account_ids,
            fargate_regions=registered.fargate_regions,
        )
    except ControlRunCompositionError:
        _close_backends(registered.backends.values())
        raise
    try:
        input_size_estimators = _bigquery_input_size_estimators(
            plans,
            sizes,
            gcp_project_id=gcp_project_id,
        )
    except Exception:
        _close_backends(registered.backends.values())
        raise ControlRunCompositionError(
            "The typed Control size-estimator binding is invalid."
        ) from None

    postgresql: _PostgreSQLResources | None = None
    selected_graph_store: GraphStore
    selected_run_store: RunStore
    try:
        if isinstance(run_store_binding, PostgreSQLRunStoreStartupBinding):
            if graph_store is not None:
                raise ControlRunCompositionError(
                    "PostgreSQL run storage owns the matching Control GraphStore selection."
                )
            postgresql = _open_postgresql_resources(
                run_store_binding,
                environ=os.environ if environ is None else environ,
            )
            selected_graph_store = postgresql.graph_store
            selected_run_store = postgresql.run_store
        elif isinstance(run_store_binding, S3RunStoreStartupBinding):
            if graph_store is None:
                raise ControlRunCompositionError(
                    "S3 run storage requires an explicit Control GraphStore."
                )
            if registered.fargate_account_ids and registered.fargate_account_ids != {
                run_store_binding.expected_bucket_owner
            }:
                raise ControlRunCompositionError(
                    "Fargate and S3 run storage must use the same AWS account."
                )
            selected_graph_store = graph_store
            selected_run_store = S3RunStore(
                run_store_binding.bucket,
                prefix=run_store_binding.prefix,
                expected_bucket_owner=run_store_binding.expected_bucket_owner,
            )
        else:
            raise ControlRunCompositionError("The run-store startup binding is unsupported.")
    except Exception as error:  # noqa: BLE001 - close already-created provider backends
        _close_backends(registered.backends.values())
        if postgresql is not None:
            with suppress(Exception):
                postgresql.pool.close()
        if isinstance(error, ControlRunCompositionError):
            raise
        raise ControlRunCompositionError("The typed Control storage binding is invalid.") from None

    composition: ControlRunComposition | None = None
    try:
        composition = compose_run_control(
            graph_store=selected_graph_store,
            store=selected_run_store,
            plans=plans,
            backends=registered.backends,
            environment=environment,
            placement_candidates=placement_candidates,
            preferred_locality=preferred_locality,
            max_cost_microusd=max_cost_microusd,
            size_class_candidates=sizes,
            default_size_class=default_size_class,
            input_size_estimators=input_size_estimators,
            reconcile_interval_seconds=reconcile_interval_seconds,
            shutdown_grace_seconds=shutdown_grace_seconds,
            start_reconciler=False,
        )
        if triggers:
            assert schedule_source_binding is not None
            source = _build_schedule_submission_source(
                binding=schedule_source_binding,
                postgresql=postgresql,
                triggers=triggers,
                graph_store=selected_graph_store,
                composition=composition,
                shutdown_grace_seconds=shutdown_grace_seconds,
            )
            composition.lifecycle.install_submission_source(source)
        composition.lifecycle.start_reconciler()
        return ControlStartup(
            graph_store=selected_graph_store,
            composition=composition,
            _shared_pool=postgresql.pool if postgresql is not None else None,
        )
    except (
        ControlOperationError,
        ControlRunCompositionError,
        ExecutionBackendError,
        OrchestrationContractError,
        RunStoreError,
        ScheduleQueueError,
        ValueError,
    ) as error:
        if composition is not None:
            with suppress(Exception):
                composition.lifecycle.close()
        if postgresql is not None:
            with suppress(Exception):
                postgresql.pool.close()
        if isinstance(error, ControlRunCompositionError):
            raise
        raise ControlRunCompositionError("The typed Control startup binding is invalid.") from None
    except Exception:  # noqa: BLE001 - never leak provider or DSN-bearing errors
        if composition is not None:
            with suppress(Exception):
                composition.lifecycle.close()
        if postgresql is not None:
            with suppress(Exception):
                postgresql.pool.close()
        raise ControlRunCompositionError("The typed Control startup binding is invalid.") from None


def _open_postgresql_resources(
    binding: PostgreSQLRunStoreStartupBinding,
    *,
    environ: Mapping[str, str],
) -> _PostgreSQLResources:
    dsn = environ.get(binding.connection_environment_variable)
    if not isinstance(dsn, str) or not dsn or len(dsn) > 8_192:
        raise ControlRunCompositionError(
            "The configured PostgreSQL connection environment variable is unavailable."
        )
    pool: PostgreSQLControlPool | None = None
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        from dander.control.postgresql_control_database import (
            PostgreSQLControlDatabase,
            PostgreSQLControlMigrator,
        )
        from dander.control.postgresql_graph_store import PostgreSQLGraphStore
        from dander.control.postgresql_run_store import PostgreSQLRunStore

        pool = cast(
            "PostgreSQLControlPool",
            ConnectionPool(
                conninfo=dsn,
                min_size=_POSTGRESQL_POOL_MIN_SIZE,
                max_size=_POSTGRESQL_POOL_MAX_SIZE,
                timeout=_POSTGRESQL_CONNECT_TIMEOUT_SECONDS,
                kwargs={"row_factory": dict_row},
                open=False,
            ),
        )
        pool.open(wait=True, timeout=_POSTGRESQL_CONNECT_TIMEOUT_SECONDS)
        database = PostgreSQLControlDatabase(pool=pool, schema_name=binding.schema_name)
        PostgreSQLControlMigrator(database).migrate()
        return _PostgreSQLResources(
            database=database,
            pool=pool,
            graph_store=PostgreSQLGraphStore(database),
            run_store=PostgreSQLRunStore(database),
            connection_environment_variable=binding.connection_environment_variable,
        )
    except ImportError:
        if pool is not None:
            with suppress(Exception):
                pool.close()
        raise ControlRunCompositionError(
            "PostgreSQL Control startup requires Dander's optional postgres dependencies."
        ) from None
    except Exception:
        if pool is not None:
            with suppress(Exception):
                pool.close()
        raise ControlRunCompositionError(
            "The PostgreSQL Control database could not be initialized."
        ) from None


def _build_schedule_submission_source(
    *,
    binding: ScheduleSourceStartupBinding,
    postgresql: _PostgreSQLResources | None,
    triggers: tuple[TriggerSpec, ...],
    graph_store: GraphStore,
    composition: ControlRunComposition,
    shutdown_grace_seconds: float,
) -> RunSubmissionSource:
    if isinstance(binding, PostgreSQLScheduleSourceStartupBinding):
        if postgresql is None:
            raise ControlRunCompositionError(
                "PostgreSQL scheduling requires PostgreSQL run storage."
            )
        if (
            binding.connection_environment_variable != postgresql.connection_environment_variable
            or binding.schema_name != postgresql.database.schema_name
        ):
            raise ControlRunCompositionError(
                "PostgreSQL run storage and scheduling must share one database binding."
            )
    schedule_resolver = ScheduledRunSubmissionResolver(
        composition.resolver.plans,
        graph_store,
        triggers,
        composition.resolver,
    )
    if isinstance(binding, SQSScheduleSourceStartupBinding):
        sqs_queue = SQSScheduleQueue(
            binding.queue_url,
            expected_account_id=binding.expected_account_id,
            expected_region=binding.region,
        )
        try:
            return ControlScheduleConsumer(
                sqs_queue,
                schedule_resolver,
                composition.lifecycle,
                shutdown_grace_seconds=shutdown_grace_seconds,
            )
        except Exception:
            with suppress(Exception):
                sqs_queue.close()
            raise
    if isinstance(binding, PostgreSQLScheduleSourceStartupBinding):
        assert postgresql is not None
        from dander.control.postgresql_schedule_queue import PostgreSQLScheduleQueue
        from dander.control.postgresql_scheduler import (
            PostgreSQLScheduler,
            PostgreSQLScheduleSubmissionSource,
        )

        postgresql_queue = PostgreSQLScheduleQueue(postgresql.database)
        consumer: ControlScheduleConsumer | None = None
        try:
            consumer = ControlScheduleConsumer(
                postgresql_queue,
                schedule_resolver,
                composition.lifecycle,
                shutdown_grace_seconds=shutdown_grace_seconds,
            )
            producer = PostgreSQLScheduler(
                postgresql.database,
                postgresql_queue,
                triggers,
                shutdown_grace_seconds=shutdown_grace_seconds,
            )
            return PostgreSQLScheduleSubmissionSource(producer, consumer)
        except Exception:
            with suppress(Exception):
                if consumer is None:
                    postgresql_queue.close()
                else:
                    consumer.close()
            raise
    raise ControlRunCompositionError("The schedule-source startup binding is unsupported.")


def _read_startup_binding(path: Path) -> bytes:
    resolved = path.expanduser().resolve()
    try:
        size = resolved.stat().st_size
        if not 1 <= size <= _MAX_STARTUP_BINDING_BYTES:
            raise ControlRunCompositionError("A Control startup binding exceeds its size bound.")
        return resolved.read_bytes()
    except ControlRunCompositionError:
        raise
    except OSError:
        raise ControlRunCompositionError(
            "A Control startup binding is unavailable or invalid."
        ) from None


def _validate_binding_compatibility(
    *,
    run_store_binding: RunStoreStartupBinding,
    schedule_source_binding: ScheduleSourceStartupBinding | None,
    fargate_account_ids: frozenset[str],
    fargate_regions: frozenset[str],
) -> None:
    if isinstance(schedule_source_binding, PostgreSQLScheduleSourceStartupBinding):
        if not isinstance(run_store_binding, PostgreSQLRunStoreStartupBinding):
            raise ControlRunCompositionError(
                "PostgreSQL scheduling requires PostgreSQL run storage."
            )
        if (
            schedule_source_binding.connection_environment_variable
            != run_store_binding.connection_environment_variable
            or schedule_source_binding.schema_name != run_store_binding.schema_name
        ):
            raise ControlRunCompositionError(
                "PostgreSQL run storage and scheduling must share one database binding."
            )
    if isinstance(schedule_source_binding, SQSScheduleSourceStartupBinding) and (
        (
            fargate_account_ids
            and fargate_account_ids != {schedule_source_binding.expected_account_id}
        )
        or (fargate_regions and fargate_regions != {schedule_source_binding.region})
    ):
        raise ControlRunCompositionError(
            "Fargate and SQS scheduling must use the same AWS account and region."
        )


def _close_backends(backends: Iterable[object]) -> None:
    for backend in backends:
        close = getattr(backend, "close", None)
        if callable(close):
            with suppress(Exception):
                close()


__all__ = [
    "ControlStartup",
    "build_control_startup",
    "load_run_store_startup_binding",
    "load_schedule_source_startup_binding",
]
