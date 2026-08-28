"""Deterministic graph planning and immutable Control backend selection."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

from dander.control.execution_plan_compiler import (
    ExecutionPlanCompiler,
    ExecutionPlanProfile,
    ManagedSparkSizeClass,
)
from dander.control.graph_store import InMemoryGraphStore
from dander.control.models import PipelineGraphDocument
from dander.control.orchestration import (
    ExecutionBackend,
    ExecutionPlan,
    OrchestrationContractError,
    RetryPolicy,
    parse_size_class_candidate_spec,
)
from dander.control.orchestration_serialization import (
    deserialize_execution_plan,
    serialize_execution_plan,
)
from dander.control.physical_planner import (
    PhysicalPlanningError,
    StaticPhysicalPlanner,
    bind_physical_plan,
)
from dander.control.run_lifecycle import (
    ExecutionBackendRegistry,
    ExecutionPlanRegistry,
    PlanRunSubmissionResolver,
)
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.physical_plan import (
    ExchangeTransport,
    PhysicalExecutionMode,
    serialize_physical_plan,
)
from dander.runtime_contract import RUNTIME_CONTRACT

NOW = datetime(2026, 8, 27, 20, tzinfo=UTC)
IMAGE = "registry.example.invalid/dander/runtime@sha256:" + "a" * 64


def _graph(*, reverse: bool = False) -> PipelineGraphDocument:
    nodes = [
        {"id": "extract_orders", "type": "source", "name": "Extract orders"},
        {"id": "clean_orders", "type": "transform", "name": "Clean orders"},
        {"id": "publish_orders", "type": "target", "name": "Publish orders"},
    ]
    edges = [
        {"from": "extract_orders", "to": "clean_orders"},
        {"from": "clean_orders", "to": "publish_orders"},
    ]
    if reverse:
        nodes.reverse()
        edges.reverse()
    return PipelineGraphDocument.model_validate(
        {"name": "Orders pipeline", "nodes": nodes, "edges": edges}
    )


def _join_graph(*, reverse: bool = False) -> PipelineGraphDocument:
    with open("graphs/keyed_join_qualification.yaml", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream)
    if reverse:
        payload["nodes"].reverse()
        payload["edges"].reverse()
    return PipelineGraphDocument.model_validate(payload)


def _template(
    backend_id: str,
    *,
    maximum_parallelism: int,
    deadline_seconds: int,
) -> ExecutionTemplate:
    return ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id="orders",
        profile_id="gcp" if backend_id == "dataproc_serverless" else "aws",
        launcher=backend_id,
        image=IMAGE,
        command=(
            "runtime",
            "execute",
            "--contract",
            RUNTIME_CONTRACT,
            "--pipeline",
            "orders",
            "--platform",
            "gcp" if backend_id == "dataproc_serverless" else "aws",
        ),
        configuration_reference=(
            "gs://dander-config/orders.json"
            if backend_id == "dataproc_serverless"
            else "s3://dander-config/orders.json"
        ),
        environment=(),
        secret_bindings=(),
        workload_identity="workload-identity",
        resources=ResourceProjection(
            cpu_millis=4_000 if backend_id == "dataproc_serverless" else 1_000,
            memory_mib=16_384 if backend_id == "dataproc_serverless" else 2_048,
            ephemeral_storage_mib=(None if backend_id == "dataproc_serverless" else 21_504),
            deadline_seconds=deadline_seconds,
            runtime_retry_count=0,
            launcher_retry_count=0,
        ),
        schedule=ScheduleProjection(
            task_count=maximum_parallelism,
            maximum_parallelism=maximum_parallelism,
            expression=None,
            time_zone=None,
            paused=True,
        ),
        network=NetworkPlacement(),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="provider_logs",
            metric_namespace="dander",
            alert_target=None,
            retention_days=None,
        ),
    )


def _execution_plan(
    *,
    graph_revision: str,
    graph_content_sha256: str,
    environment: str,
    backend_id: str,
    physical_mode: PhysicalExecutionMode,
) -> ExecutionPlan:
    physical = StaticPhysicalPlanner().plan(
        _graph(),
        pipeline_id="orders",
        execution_mode=physical_mode,
    )
    deadline = 600 if backend_id == "dataproc_serverless" else 300
    template = bind_physical_plan(
        _template(
            backend_id,
            maximum_parallelism=physical.maximum_parallelism,
            deadline_seconds=deadline,
        ),
        physical,
    )
    return ExecutionPlan(
        plan_id=f"{environment}-orders",
        environment=environment,
        project="demo",
        graph="orders",
        graph_revision=graph_revision,
        graph_content_sha256=graph_content_sha256,
        backend_id=backend_id,
        profile_id=template.profile_id,
        image=template.image,
        execution_template=template,
        deadline_seconds=deadline,
        retry_policy=RetryPolicy(max_attempts=1),
        physical_plan=physical,
    )


def test_static_planner_is_deterministic_across_canonical_graph_order() -> None:
    planner = StaticPhysicalPlanner()

    fused = planner.plan(
        _graph(),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
    )
    reordered_fused = planner.plan(
        _graph(reverse=True),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
    )
    distributed = planner.plan(
        _graph(),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
    )
    reordered_distributed = planner.plan(
        _graph(reverse=True),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
    )

    assert reordered_fused == fused
    assert reordered_distributed == distributed
    assert fused.stages[0].operators == (
        "clean_orders",
        "extract_orders",
        "publish_orders",
    )
    assert fused.maximum_parallelism == 1
    assert distributed.maximum_parallelism == 2
    assert distributed.partition_count == 4
    assert distributed.exchanges[0].transport is ExchangeTransport.OBJECT_STORE
    assert distributed.revision != fused.revision


def test_distributed_planner_accepts_one_bounded_static_partition_count() -> None:
    planner = StaticPhysicalPlanner()

    large = planner.plan(
        _graph(),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        distributed_partitions=4,
    )

    assert large.maximum_parallelism == 4
    assert [stage.partition_count for stage in large.stages] == [4, 4]
    assert large.exchanges[0].partition_count == 4
    with pytest.raises(PhysicalPlanningError, match="outside its static bound"):
        planner.plan(
            _graph(),
            pipeline_id="orders",
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
            distributed_partitions=2_001,
        )
    with pytest.raises(PhysicalPlanningError, match="Fused planning"):
        planner.plan(
            _graph(),
            pipeline_id="orders",
            execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
            distributed_partitions=2,
        )


def test_planner_compiles_one_deterministic_keyed_join_shape() -> None:
    planner = StaticPhysicalPlanner()

    fused = planner.plan(
        _join_graph(),
        pipeline_id="keyed_join_qualification",
        execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
    )
    distributed = planner.plan(
        _join_graph(),
        pipeline_id="keyed_join_qualification",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        distributed_partitions=3,
    )
    reordered = planner.plan(
        _join_graph(reverse=True),
        pipeline_id="keyed_join_qualification",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        distributed_partitions=3,
    )

    assert reordered == distributed
    assert [stage.stage_id for stage in distributed.stages] == [
        "extract-left",
        "extract-right",
        "join",
    ]
    assert [stage.operators for stage in distributed.stages] == [
        ("posts",),
        ("comments",),
        ("curated_post_comments", "joined_post_comments"),
    ]
    assert [exchange.partition_keys for exchange in distributed.exchanges] == [
        ("id",),
        ("postId",),
    ]
    assert all(exchange.partitioning.value == "hash" for exchange in distributed.exchanges)
    assert distributed.maximum_parallelism == 3
    assert distributed.revision != fused.revision
    assert fused.stages[0].operators == (
        "comments",
        "curated_post_comments",
        "joined_post_comments",
        "posts",
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["nodes"][2]["config"]["join"].update(type="left"), "one inner"),
        (
            lambda payload: payload["nodes"][2]["config"].update(
                operations=[{"kind": "trim_whitespace", "params": {"field": "title"}}]
            ),
            "one inner",
        ),
        (
            lambda payload: payload["nodes"][1]["fields"][2].update(type="STRING"),
            "same declared type",
        ),
    ],
)
def test_keyed_join_planner_fails_closed_outside_the_bounded_shape(
    mutation: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    payload = _join_graph().model_dump(mode="json", by_alias=True)
    mutation(payload)
    graph = PipelineGraphDocument.model_validate(payload)

    with pytest.raises(PhysicalPlanningError, match=message):
        StaticPhysicalPlanner().plan(
            graph,
            pipeline_id="keyed_join_qualification",
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        )


def test_distributed_planner_fails_closed_for_an_unsupported_graph_shape() -> None:
    payload = _graph().model_dump(mode="json", by_alias=True)
    payload["nodes"].append(
        {"id": "audit_orders", "type": "target", "name": "Audit orders", "config": {}}
    )
    payload["edges"].append(
        {
            "from": "clean_orders",
            "to": "audit_orders",
            "metadata": {},
            "mappings": [],
            "join": None,
        }
    )
    graph = PipelineGraphDocument.model_validate(payload)

    with pytest.raises(PhysicalPlanningError, match="exactly one"):
        StaticPhysicalPlanner().plan(
            graph,
            pipeline_id="orders",
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        )


def test_planner_fails_closed_for_an_extension_node_type() -> None:
    payload = _graph().model_dump(mode="json", by_alias=True)
    payload["nodes"].append(
        {"id": "notify_orders", "type": "notification", "name": "Notify", "config": {}}
    )
    graph = PipelineGraphDocument.model_validate(payload)

    with pytest.raises(PhysicalPlanningError, match="node type"):
        StaticPhysicalPlanner().plan(
            graph,
            pipeline_id="orders",
            execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
        )


def test_distributed_planner_fails_closed_for_a_join() -> None:
    payload = _graph().model_dump(mode="json", by_alias=True)
    payload["nodes"][0]["fields"] = [{"name": "order_id", "type": "string"}]
    payload["nodes"][1]["fields"] = [{"name": "order_id", "type": "string"}]
    payload["edges"][0]["join"] = {
        "type": "inner",
        "keys": [{"left": "order_id", "right": "order_id"}],
    }
    graph = PipelineGraphDocument.model_validate(payload)

    with pytest.raises(PhysicalPlanningError, match="joins"):
        StaticPhysicalPlanner().plan(
            graph,
            pipeline_id="orders",
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        )


def test_binding_appends_exact_plan_and_enforces_planned_parallelism() -> None:
    physical = StaticPhysicalPlanner().plan(
        _graph(),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
    )
    template = _template(
        "dataproc_serverless",
        maximum_parallelism=2,
        deadline_seconds=600,
    )

    bound = bind_physical_plan(template, physical)

    assert bound.command[-2:] == (
        "--physical-plan",
        serialize_physical_plan(physical).decode("utf-8"),
    )
    with pytest.raises(PhysicalPlanningError, match="already contains"):
        bind_physical_plan(bound, physical)
    with pytest.raises(PhysicalPlanningError, match="maximum parallelism"):
        bind_physical_plan(
            _template("dataproc_serverless", maximum_parallelism=1, deadline_seconds=600),
            physical,
        )


def test_control_selects_existing_backend_for_same_graph_planned_two_ways() -> None:
    graph_store = InMemoryGraphStore(
        clock=lambda: NOW,
        revision_factory=lambda: "graph-r1",
    )
    graph = graph_store.create(
        "demo",
        "orders",
        _graph(),
        idempotency_key="graph-key-0001",
    )
    fused = _execution_plan(
        graph_revision=graph.revision,
        graph_content_sha256=graph.content_sha256,
        environment="aws",
        backend_id="fargate",
        physical_mode=PhysicalExecutionMode.FUSED_CONTAINER,
    )
    distributed = _execution_plan(
        graph_revision=graph.revision,
        graph_content_sha256=graph.content_sha256,
        environment="spark",
        backend_id="dataproc_serverless",
        physical_mode=PhysicalExecutionMode.DISTRIBUTED,
    )
    plans = ExecutionPlanRegistry((fused, distributed))
    resolver = PlanRunSubmissionResolver(plans, "aws")
    fargate_backend = cast("ExecutionBackend", object())
    spark_backend = cast("ExecutionBackend", object())
    backends = ExecutionBackendRegistry(
        {"fargate": fargate_backend, "dataproc_serverless": spark_backend}
    )

    fused_submission = resolver.resolve(
        graph,
        environment="aws",
        idempotency_key="run-key-0001",
        requested_at=NOW,
    )
    spark_submission = resolver.resolve(
        graph,
        environment="spark",
        idempotency_key="run-key-0002",
        requested_at=NOW,
    )

    selected_fused = plans.for_submission(fused_submission)
    selected_spark = plans.for_submission(spark_submission)
    assert selected_fused.physical_plan is not None
    assert selected_spark.physical_plan is not None
    assert selected_fused.physical_plan.execution_mode is PhysicalExecutionMode.FUSED_CONTAINER
    assert selected_spark.physical_plan.execution_mode is PhysicalExecutionMode.DISTRIBUTED
    assert backends.require(selected_fused.backend_id) is fargate_backend
    assert backends.require(selected_spark.backend_id) is spark_backend
    restored = deserialize_execution_plan(serialize_execution_plan(selected_spark))
    assert restored == selected_spark
    assert restored.revision == selected_spark.revision
    assert restored.physical_plan is not None
    assert restored.physical_plan.revision == selected_spark.physical_plan.revision


def test_execution_plan_compiler_derives_identity_and_removes_embedded_schedule() -> None:
    graph_store = InMemoryGraphStore(
        clock=lambda: NOW,
        revision_factory=lambda: "graph-r1",
    )
    graph = graph_store.create(
        "demo",
        "orders",
        _graph(),
        idempotency_key="graph-key-0001",
    )
    scheduled_template = replace(
        _template("fargate", maximum_parallelism=1, deadline_seconds=300),
        schedule=ScheduleProjection(
            task_count=1,
            maximum_parallelism=1,
            expression="cron(0 6 * * ? *)",
            time_zone="America/New_York",
            paused=False,
        ),
    )
    profile = ExecutionPlanProfile(
        plan_id="aws-orders",
        environment="aws",
        execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
    )
    compiler = ExecutionPlanCompiler()

    plan = compiler.compile(graph, profile, scheduled_template)
    repeated = compiler.compile(graph, profile, scheduled_template)

    assert repeated == plan
    assert repeated.revision == plan.revision
    assert (plan.project, plan.graph) == (graph.project, graph.graph)
    assert (plan.graph_revision, plan.graph_content_sha256) == (
        graph.revision,
        graph.content_sha256,
    )
    assert plan.backend_id == scheduled_template.launcher
    assert plan.profile_id == scheduled_template.profile_id
    assert plan.image == scheduled_template.image
    assert plan.deadline_seconds == scheduled_template.resources.deadline_seconds
    assert plan.retry_policy.max_attempts == scheduled_template.resources.launcher_retry_count + 1
    assert plan.execution_template.schedule == replace(
        scheduled_template.schedule,
        expression=None,
        time_zone=None,
    )
    assert plan.execution_template.command[:-2] == scheduled_template.command
    assert plan.execution_template.command[-2] == "--physical-plan"


def test_execution_plan_compiler_binds_managed_spark_to_the_control_graph() -> None:
    graph = InMemoryGraphStore(
        clock=lambda: NOW,
        revision_factory=lambda: "graph-r1",
    ).create(
        "demo",
        "orders",
        _graph(),
        idempotency_key="graph-key-0001",
    )
    template = _template(
        "dataproc_serverless",
        maximum_parallelism=2,
        deadline_seconds=600,
    )
    profile = ExecutionPlanProfile(
        plan_id="spark-orders",
        environment="spark",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
    )

    plan = ExecutionPlanCompiler().compile(graph, profile, template)

    assert plan.execution_template.command[-4:-2] == (
        "--graph-content-sha256",
        graph.content_sha256,
    )
    assert plan.execution_template.command[-2] == "--physical-plan"

    duplicate = replace(
        template,
        command=(*template.command, "--graph-content-sha256", "a" * 64),
    )
    with pytest.raises(
        ValueError,
        match="already contains a graph content identity",
    ):
        ExecutionPlanCompiler().compile(graph, profile, duplicate)


def test_compiler_materializes_canonical_managed_spark_size_classes() -> None:
    graph = InMemoryGraphStore(
        clock=lambda: NOW,
        revision_factory=lambda: "graph-r1",
    ).create(
        "demo",
        "orders",
        _graph(),
        idempotency_key="graph-key-0001",
    )
    template = _template(
        "dataproc_serverless",
        maximum_parallelism=2,
        deadline_seconds=600,
    )
    profile = ExecutionPlanProfile(
        plan_id="spark-orders",
        environment="spark",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
    )
    small = ManagedSparkSizeClass("small", 1_000, 2, 4_000, 16_384)
    large = ManagedSparkSizeClass("large", 10_000, 4, 8_000, 32_768)
    compiler = ExecutionPlanCompiler()

    compiled = compiler.compile_managed_spark_size_classes(graph, profile, template, (large, small))
    repeated = compiler.compile_managed_spark_size_classes(graph, profile, template, (small, large))

    assert repeated == compiled
    assert tuple(plan.revision for plan in compiled.plans) == tuple(
        sorted(plan.revision for plan in compiled.plans)
    )
    assert (
        tuple(parse_size_class_candidate_spec(spec) for spec in compiled.size_candidate_specs)
        == compiled.size_class_candidates
    )
    assert (
        tuple(deserialize_execution_plan(value.encode()) for value in compiled.execution_plan_json)
        == compiled.plans
    )
    by_class = {
        candidate.size_class: next(
            plan for plan in compiled.plans if plan.revision == candidate.plan_revision
        )
        for candidate in compiled.size_class_candidates
    }
    assert by_class["small"].physical_plan is not None
    assert by_class["small"].physical_plan.maximum_parallelism == 2
    assert by_class["small"].execution_template.schedule.task_count == 2
    assert by_class["large"].physical_plan is not None
    assert by_class["large"].physical_plan.maximum_parallelism == 4
    assert by_class["large"].execution_template.schedule.task_count == 4
    assert by_class["large"].execution_template.resources.cpu_millis == 8_000
    assert by_class["large"].execution_template.resources.memory_mib == 32_768


def test_managed_spark_execution_plan_requires_distributed_physical_execution() -> None:
    template = _template(
        "dataproc_serverless",
        maximum_parallelism=2,
        deadline_seconds=600,
    )

    with pytest.raises(OrchestrationContractError, match="requires distributed"):
        ExecutionPlan(
            plan_id="spark-orders",
            environment="spark",
            project="demo",
            graph="orders",
            graph_revision="graph-r1",
            graph_content_sha256="a" * 64,
            backend_id="dataproc_serverless",
            profile_id=template.profile_id,
            image=template.image,
            execution_template=template,
            deadline_seconds=600,
            retry_policy=RetryPolicy(max_attempts=1),
        )

    fused = StaticPhysicalPlanner().plan(
        _graph(),
        pipeline_id="orders",
        execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
    )
    with pytest.raises(OrchestrationContractError, match="requires distributed"):
        ExecutionPlan(
            plan_id="spark-orders",
            environment="spark",
            project="demo",
            graph="orders",
            graph_revision="graph-r1",
            graph_content_sha256="a" * 64,
            backend_id="dataproc_serverless",
            profile_id=template.profile_id,
            image=template.image,
            execution_template=bind_physical_plan(template, fused),
            deadline_seconds=600,
            retry_policy=RetryPolicy(max_attempts=1),
            physical_plan=fused,
        )
