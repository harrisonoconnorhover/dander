"""Static physical-plan contract and canonical serialization."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from dander.physical_plan import (
    ExchangeTransport,
    PartitioningStrategy,
    PhysicalExchange,
    PhysicalExecutionMode,
    PhysicalPlan,
    PhysicalPlanError,
    PhysicalStage,
    deserialize_physical_plan,
    fused_container_physical_plan,
    serialize_physical_plan,
)


def _distributed_plan() -> PhysicalPlan:
    return PhysicalPlan(
        pipeline_id="greenhouse_jobs",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        stages=(
            PhysicalStage(
                stage_id="extract",
                operators=("extract.jobs",),
                partition_count=2,
            ),
            PhysicalStage(
                stage_id="transform",
                operators=("transform.jobs",),
                partition_count=4,
                depends_on=("extract",),
            ),
        ),
        exchanges=(
            PhysicalExchange(
                exchange_id="extract_to_transform",
                producer_stage_id="extract",
                consumer_stage_id="transform",
                transport=ExchangeTransport.OBJECT_STORE,
                partitioning=PartitioningStrategy.HASH,
                partition_count=4,
                partition_keys=("job_id",),
            ),
        ),
        maximum_parallelism=4,
    )


def test_physical_plan_round_trips_canonical_static_topology() -> None:
    plan = _distributed_plan()

    encoded = serialize_physical_plan(plan)
    envelope = json.loads(encoded)

    assert deserialize_physical_plan(encoded) == plan
    assert envelope["schema"] == "io.dander.physical-plan/v1"
    assert envelope["revision"] == plan.revision
    assert plan.partition_count == 6
    assert encoded == serialize_physical_plan(deserialize_physical_plan(encoded))


def test_physical_plan_rejects_revision_tampering_and_noncanonical_bytes() -> None:
    encoded = serialize_physical_plan(_distributed_plan())
    envelope = json.loads(encoded)
    envelope["plan"]["maximum_parallelism"] = 3

    with pytest.raises(PhysicalPlanError, match="revision"):
        deserialize_physical_plan(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        )
    with pytest.raises(PhysicalPlanError, match="canonical"):
        deserialize_physical_plan(json.dumps(json.loads(encoded), indent=2).encode())


def test_physical_plan_requires_exchange_for_each_dependency() -> None:
    plan = _distributed_plan()

    with pytest.raises(PhysicalPlanError, match="exactly one exchange"):
        replace(plan, exchanges=())


def test_physical_plan_rejects_cycles() -> None:
    with pytest.raises(PhysicalPlanError, match="cycle"):
        PhysicalPlan(
            pipeline_id="greenhouse_jobs",
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
            stages=(
                PhysicalStage(
                    stage_id="first",
                    operators=("first",),
                    partition_count=1,
                    depends_on=("second",),
                ),
                PhysicalStage(
                    stage_id="second",
                    operators=("second",),
                    partition_count=1,
                    depends_on=("first",),
                ),
            ),
            exchanges=(
                PhysicalExchange(
                    exchange_id="first_to_second",
                    producer_stage_id="first",
                    consumer_stage_id="second",
                    transport=ExchangeTransport.OBJECT_STORE,
                    partitioning=PartitioningStrategy.SINGLE,
                    partition_count=1,
                ),
                PhysicalExchange(
                    exchange_id="second_to_first",
                    producer_stage_id="second",
                    consumer_stage_id="first",
                    transport=ExchangeTransport.OBJECT_STORE,
                    partitioning=PartitioningStrategy.SINGLE,
                    partition_count=1,
                ),
            ),
            maximum_parallelism=1,
        )


def test_fused_container_plan_keeps_one_existing_worker_partition() -> None:
    plan = fused_container_physical_plan("greenhouse_jobs")

    assert plan.execution_mode is PhysicalExecutionMode.FUSED_CONTAINER
    assert plan.partition_count == 1
    assert plan.maximum_parallelism == 1
    assert plan.exchanges == ()


def test_fused_container_plan_rejects_distributed_transport() -> None:
    with pytest.raises(PhysicalPlanError, match="in-memory"):
        PhysicalPlan(
            pipeline_id="greenhouse_jobs",
            execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
            stages=(
                PhysicalStage(
                    stage_id="extract",
                    operators=("extract",),
                    partition_count=1,
                ),
                PhysicalStage(
                    stage_id="transform",
                    operators=("transform",),
                    partition_count=1,
                    depends_on=("extract",),
                ),
            ),
            exchanges=(
                PhysicalExchange(
                    exchange_id="extract_to_transform",
                    producer_stage_id="extract",
                    consumer_stage_id="transform",
                    transport=ExchangeTransport.OBJECT_STORE,
                    partitioning=PartitioningStrategy.SINGLE,
                    partition_count=1,
                ),
            ),
            maximum_parallelism=1,
        )
