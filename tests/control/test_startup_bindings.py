"""Closed canonical startup binding contracts for hosted Control durability."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from dander.control.startup_bindings import (
    PostgreSQLRunStoreStartupBinding,
    PostgreSQLScheduleSourceStartupBinding,
    S3RunStoreStartupBinding,
    SQSScheduleSourceStartupBinding,
    StartupBindingError,
    deserialize_run_store_startup_binding,
    deserialize_schedule_source_startup_binding,
    serialize_run_store_startup_binding,
    serialize_schedule_source_startup_binding,
)


def test_run_store_union_round_trips_as_exact_deterministic_json() -> None:
    bindings = (
        S3RunStoreStartupBinding(
            bucket="dander-control-runs",
            prefix="dander-control/v1",
            expected_bucket_owner="123456789012",
            region="us-east-1",
        ),
        PostgreSQLRunStoreStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        ),
    )

    for binding in bindings:
        serialized = serialize_run_store_startup_binding(binding)
        assert deserialize_run_store_startup_binding(serialized) == binding
        assert serialized == serialize_run_store_startup_binding(binding)
        assert not serialized.endswith(b"\n")

    assert serialize_run_store_startup_binding(bindings[1]) == (
        b'{"binding":{"connection_environment_variable":"DANDER_CONTROL_DATABASE_URL",'
        b'"kind":"postgresql","schema_name":"dander_control"},'
        b'"schema":"io.dander.control.run-store-startup-binding/v1"}'
    )


def test_schedule_source_union_round_trips_as_exact_deterministic_json() -> None:
    bindings = (
        SQSScheduleSourceStartupBinding(
            queue_url=("https://sqs.us-east-1.amazonaws.com/123456789012/dander-schedule-wakeups"),
            expected_account_id="123456789012",
            region="us-east-1",
        ),
        PostgreSQLScheduleSourceStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        ),
    )

    for binding in bindings:
        serialized = serialize_schedule_source_startup_binding(binding)
        assert deserialize_schedule_source_startup_binding(serialized) == binding
        assert serialized == serialize_schedule_source_startup_binding(binding)
        assert not serialized.endswith(b"\n")

    assert serialize_schedule_source_startup_binding(bindings[1]) == (
        b'{"binding":{"connection_environment_variable":"DANDER_CONTROL_DATABASE_URL",'
        b'"kind":"postgresql","schema_name":"dander_control"},'
        b'"schema":"io.dander.control.schedule-source-startup-binding/v1"}'
    )


def test_postgresql_arms_are_immutable_and_carry_references_not_secret_values() -> None:
    binding = PostgreSQLRunStoreStartupBinding(
        connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
        schema_name="dander_control",
    )

    assert tuple(binding.__dataclass_fields__) == (
        "connection_environment_variable",
        "schema_name",
        "kind",
    )
    with pytest.raises(FrozenInstanceError):
        binding.schema_name = "other"  # type: ignore[misc]

    for environment_variable in (
        "postgresql://user:password@database/control",
        "DANDER-CONTROL-DATABASE-URL",
        "dander_control_database_url",
        "A" * 129,
    ):
        with pytest.raises(StartupBindingError, match="environment variable"):
            PostgreSQLRunStoreStartupBinding(
                connection_environment_variable=environment_variable,
                schema_name="dander_control",
            )


@pytest.mark.parametrize(
    "schema_name",
    ["", "Dander_Control", "1dander", "dander-control", "a" * 64],
)
def test_postgresql_schema_is_a_bounded_unquoted_identifier(schema_name: str) -> None:
    with pytest.raises(StartupBindingError, match="schema name"):
        PostgreSQLScheduleSourceStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name=schema_name,
        )


def test_run_store_parser_rejects_unknown_secret_bearing_and_missing_fields() -> None:
    valid = json.loads(
        serialize_run_store_startup_binding(
            PostgreSQLRunStoreStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="dander_control",
            )
        )
    )

    for field, value in (
        ("dsn", "postgresql://user:password@database/control"),
        ("password", "literal-secret"),
        ("credentials", {"username": "control"}),
        ("future_option", True),
    ):
        mutated = json.loads(json.dumps(valid))
        mutated["binding"][field] = value
        with pytest.raises(StartupBindingError, match="fields are invalid"):
            deserialize_run_store_startup_binding(_canonical_json(mutated))

    del valid["binding"]["schema_name"]
    with pytest.raises(StartupBindingError, match="fields are invalid"):
        deserialize_run_store_startup_binding(_canonical_json(valid))


def test_schedule_parser_rejects_unknown_secret_bearing_and_missing_fields() -> None:
    valid = json.loads(
        serialize_schedule_source_startup_binding(
            PostgreSQLScheduleSourceStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="dander_control",
            )
        )
    )

    for field in ("dsn", "password", "secret", "future_option"):
        mutated = json.loads(json.dumps(valid))
        mutated["binding"][field] = "must-not-be-accepted"
        with pytest.raises(StartupBindingError, match="fields are invalid"):
            deserialize_schedule_source_startup_binding(_canonical_json(mutated))

    del valid["binding"]["connection_environment_variable"]
    with pytest.raises(StartupBindingError, match="fields are invalid"):
        deserialize_schedule_source_startup_binding(_canonical_json(valid))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data + b"\n",
        lambda data: data.replace(b'":"', b'": "', 1),
        lambda data: b" " + data,
    ],
)
def test_parsers_reject_noncanonical_json(mutate: object) -> None:
    run_bytes = serialize_run_store_startup_binding(
        PostgreSQLRunStoreStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        )
    )
    schedule_bytes = serialize_schedule_source_startup_binding(
        PostgreSQLScheduleSourceStartupBinding(
            connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
            schema_name="dander_control",
        )
    )
    transform = mutate
    assert callable(transform)
    with pytest.raises(StartupBindingError, match="not canonical"):
        deserialize_run_store_startup_binding(transform(run_bytes))
    with pytest.raises(StartupBindingError, match="not canonical"):
        deserialize_schedule_source_startup_binding(transform(schedule_bytes))


def test_s3_binding_rejects_unsafe_or_unbounded_coordinates() -> None:
    valid = {
        "bucket": "dander-control-runs",
        "prefix": "dander-control/v1",
        "expected_bucket_owner": "123456789012",
        "region": "us-east-1",
    }

    for field, value in (
        ("bucket", "Dander-Control-Runs"),
        ("bucket", "dander..control"),
        ("prefix", "dander-control/../runs"),
        ("prefix", "/dander-control/v1"),
        ("expected_bucket_owner", "1234"),
        ("region", "US-EAST-1"),
        ("region", "us-east-123456789012345678901234567890"),
    ):
        values = {**valid, field: value}
        with pytest.raises(StartupBindingError):
            S3RunStoreStartupBinding(**values)


def test_sqs_binding_fences_queue_url_to_declared_owner_and_region() -> None:
    valid = {
        "queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/dander-wakeups",
        "expected_account_id": "123456789012",
        "region": "us-east-1",
    }
    assert SQSScheduleSourceStartupBinding(**valid).kind == "sqs"

    for field, value in (
        (
            "queue_url",
            "https://sqs.us-west-2.amazonaws.com/123456789012/dander-wakeups",
        ),
        (
            "queue_url",
            "https://sqs.us-east-1.amazonaws.com/999999999999/dander-wakeups",
        ),
        ("queue_url", "https://user:password@sqs.us-east-1.amazonaws.com/x/y"),
        ("expected_account_id", "9999"),
        ("region", "not-a-region"),
    ):
        values = {**valid, field: value}
        with pytest.raises(StartupBindingError):
            SQSScheduleSourceStartupBinding(**values)


def test_parsers_reject_wrong_schema_kind_envelope_and_oversize() -> None:
    valid = json.loads(
        serialize_run_store_startup_binding(
            PostgreSQLRunStoreStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="dander_control",
            )
        )
    )

    mutations = (
        {**valid, "schema": "io.dander.control.run-store-startup-binding/v2"},
        {**valid, "extra": True},
        {**valid, "binding": {**valid["binding"], "kind": "mysql"}},
    )
    for mutation in mutations:
        with pytest.raises(StartupBindingError):
            deserialize_run_store_startup_binding(_canonical_json(mutation))

    with pytest.raises(StartupBindingError, match="size"):
        deserialize_run_store_startup_binding(b"{" + b" " * (16 * 1024))


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
