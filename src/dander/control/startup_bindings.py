"""Closed provider-neutral startup bindings for hosted Control durability.

These records contain only non-secret resource coordinates.  PostgreSQL credentials remain in
the named process environment variable and are resolved only by the selected adapter boundary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast
from urllib.parse import urlsplit

RUN_STORE_STARTUP_BINDING_SCHEMA = "io.dander.control.run-store-startup-binding/v1"
SCHEDULE_SOURCE_STARTUP_BINDING_SCHEMA = "io.dander.control.schedule-source-startup-binding/v1"

_MAX_BINDING_BYTES = 16 * 1024
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_POSTGRESQL_SCHEMA = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_AWS_ACCOUNT = re.compile(r"^[0-9]{12}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+$")
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_S3_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}[A-Za-z0-9]$")
_SQS_QUEUE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class StartupBindingError(ValueError):
    """A startup binding is unsupported, invalid, or non-canonical."""


@dataclass(frozen=True, slots=True)
class S3RunStoreStartupBinding:
    """Non-secret coordinates for the existing S3 ``RunStore`` adapter."""

    bucket: str
    prefix: str
    expected_bucket_owner: str
    region: str
    kind: Literal["s3"] = field(default="s3", init=False)

    def __post_init__(self) -> None:
        _validate_s3_bucket(self.bucket)
        _validate_s3_prefix(self.prefix)
        _validate_aws_account(self.expected_bucket_owner, "S3 bucket owner")
        _validate_aws_region(self.region)


@dataclass(frozen=True, slots=True)
class PostgreSQLRunStoreStartupBinding:
    """Reference a PostgreSQL connection without carrying its secret DSN."""

    connection_environment_variable: str
    schema_name: str
    kind: Literal["postgresql"] = field(default="postgresql", init=False)

    def __post_init__(self) -> None:
        _validate_environment_variable(self.connection_environment_variable)
        _validate_postgresql_schema(self.schema_name)


type RunStoreStartupBinding = S3RunStoreStartupBinding | PostgreSQLRunStoreStartupBinding


@dataclass(frozen=True, slots=True)
class SQSScheduleSourceStartupBinding:
    """Non-secret coordinates for the existing SQS schedule source."""

    queue_url: str
    expected_account_id: str
    region: str
    kind: Literal["sqs"] = field(default="sqs", init=False)

    def __post_init__(self) -> None:
        _validate_aws_account(self.expected_account_id, "SQS account")
        _validate_aws_region(self.region)
        _validate_sqs_queue_url(
            self.queue_url,
            expected_account_id=self.expected_account_id,
            region=self.region,
        )


@dataclass(frozen=True, slots=True)
class PostgreSQLScheduleSourceStartupBinding:
    """Reference a PostgreSQL schedule source without carrying its secret DSN."""

    connection_environment_variable: str
    schema_name: str
    kind: Literal["postgresql"] = field(default="postgresql", init=False)

    def __post_init__(self) -> None:
        _validate_environment_variable(self.connection_environment_variable)
        _validate_postgresql_schema(self.schema_name)


type ScheduleSourceStartupBinding = (
    SQSScheduleSourceStartupBinding | PostgreSQLScheduleSourceStartupBinding
)


def serialize_run_store_startup_binding(binding: RunStoreStartupBinding) -> bytes:
    """Return deterministic versioned JSON for one closed run-store union arm."""
    return _canonical_json(
        {
            "schema": RUN_STORE_STARTUP_BINDING_SCHEMA,
            "binding": _run_store_payload(binding),
        }
    )


def deserialize_run_store_startup_binding(data: bytes) -> RunStoreStartupBinding:
    """Load a canonical run-store startup binding and reject every unknown field."""
    try:
        envelope = _load_envelope(data, RUN_STORE_STARTUP_BINDING_SCHEMA)
        binding = _run_store_from_payload(envelope["binding"])
        if data != serialize_run_store_startup_binding(binding):
            raise StartupBindingError("run-store startup binding is not canonical")
        return binding
    except StartupBindingError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise StartupBindingError("run-store startup binding is invalid") from error


def serialize_schedule_source_startup_binding(binding: ScheduleSourceStartupBinding) -> bytes:
    """Return deterministic versioned JSON for one closed schedule-source union arm."""
    return _canonical_json(
        {
            "schema": SCHEDULE_SOURCE_STARTUP_BINDING_SCHEMA,
            "binding": _schedule_source_payload(binding),
        }
    )


def deserialize_schedule_source_startup_binding(data: bytes) -> ScheduleSourceStartupBinding:
    """Load a canonical schedule-source binding and reject every unknown field."""
    try:
        envelope = _load_envelope(data, SCHEDULE_SOURCE_STARTUP_BINDING_SCHEMA)
        binding = _schedule_source_from_payload(envelope["binding"])
        if data != serialize_schedule_source_startup_binding(binding):
            raise StartupBindingError("schedule-source startup binding is not canonical")
        return binding
    except StartupBindingError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise StartupBindingError("schedule-source startup binding is invalid") from error


def _run_store_payload(binding: RunStoreStartupBinding) -> dict[str, str]:
    if isinstance(binding, S3RunStoreStartupBinding):
        return {
            "kind": binding.kind,
            "bucket": binding.bucket,
            "prefix": binding.prefix,
            "expected_bucket_owner": binding.expected_bucket_owner,
            "region": binding.region,
        }
    if isinstance(binding, PostgreSQLRunStoreStartupBinding):
        return {
            "kind": binding.kind,
            "connection_environment_variable": binding.connection_environment_variable,
            "schema_name": binding.schema_name,
        }
    raise StartupBindingError("run-store startup binding kind is unsupported")


def _run_store_from_payload(value: object) -> RunStoreStartupBinding:
    values = _mapping(value, "run-store binding")
    kind = _string(values.get("kind"), "run-store binding kind")
    if kind == "s3":
        _require_fields(
            values,
            {
                "kind",
                "bucket",
                "prefix",
                "expected_bucket_owner",
                "region",
            },
            "S3 run-store binding",
        )
        return S3RunStoreStartupBinding(
            bucket=_string(values["bucket"], "S3 bucket"),
            prefix=_string(values["prefix"], "S3 prefix"),
            expected_bucket_owner=_string(values["expected_bucket_owner"], "S3 bucket owner"),
            region=_string(values["region"], "AWS region"),
        )
    if kind == "postgresql":
        _require_fields(
            values,
            {"kind", "connection_environment_variable", "schema_name"},
            "PostgreSQL run-store binding",
        )
        return PostgreSQLRunStoreStartupBinding(
            connection_environment_variable=_string(
                values["connection_environment_variable"],
                "PostgreSQL connection environment variable",
            ),
            schema_name=_string(values["schema_name"], "PostgreSQL schema"),
        )
    raise StartupBindingError("run-store startup binding kind is unsupported")


def _schedule_source_payload(binding: ScheduleSourceStartupBinding) -> dict[str, str]:
    if isinstance(binding, SQSScheduleSourceStartupBinding):
        return {
            "kind": binding.kind,
            "queue_url": binding.queue_url,
            "expected_account_id": binding.expected_account_id,
            "region": binding.region,
        }
    if isinstance(binding, PostgreSQLScheduleSourceStartupBinding):
        return {
            "kind": binding.kind,
            "connection_environment_variable": binding.connection_environment_variable,
            "schema_name": binding.schema_name,
        }
    raise StartupBindingError("schedule-source startup binding kind is unsupported")


def _schedule_source_from_payload(value: object) -> ScheduleSourceStartupBinding:
    values = _mapping(value, "schedule-source binding")
    kind = _string(values.get("kind"), "schedule-source binding kind")
    if kind == "sqs":
        _require_fields(
            values,
            {"kind", "queue_url", "expected_account_id", "region"},
            "SQS schedule-source binding",
        )
        return SQSScheduleSourceStartupBinding(
            queue_url=_string(values["queue_url"], "SQS queue URL"),
            expected_account_id=_string(values["expected_account_id"], "SQS account"),
            region=_string(values["region"], "AWS region"),
        )
    if kind == "postgresql":
        _require_fields(
            values,
            {"kind", "connection_environment_variable", "schema_name"},
            "PostgreSQL schedule-source binding",
        )
        return PostgreSQLScheduleSourceStartupBinding(
            connection_environment_variable=_string(
                values["connection_environment_variable"],
                "PostgreSQL connection environment variable",
            ),
            schema_name=_string(values["schema_name"], "PostgreSQL schema"),
        )
    raise StartupBindingError("schedule-source startup binding kind is unsupported")


def _load_envelope(data: bytes, expected_schema: str) -> Mapping[str, object]:
    if not isinstance(data, bytes) or not data or len(data) > _MAX_BINDING_BYTES:
        raise StartupBindingError("startup binding size is invalid")
    value = json.loads(data)
    envelope = _mapping(value, "startup-binding envelope")
    _require_fields(envelope, {"schema", "binding"}, "startup-binding envelope")
    if _string(envelope["schema"], "startup-binding schema") != expected_schema:
        raise StartupBindingError("startup binding schema is unsupported")
    return envelope


def _validate_environment_variable(value: str) -> None:
    if not isinstance(value, str) or _ENVIRONMENT_VARIABLE.fullmatch(value) is None:
        raise StartupBindingError("PostgreSQL connection environment variable is invalid")


def _validate_postgresql_schema(value: str) -> None:
    if not isinstance(value, str) or _POSTGRESQL_SCHEMA.fullmatch(value) is None:
        raise StartupBindingError("PostgreSQL schema name is invalid")


def _validate_s3_bucket(value: str) -> None:
    if (
        not isinstance(value, str)
        or _S3_BUCKET.fullmatch(value) is None
        or ".." in value
        or ".-" in value
        or "-." in value
        or value.endswith("--x-s3")
    ):
        raise StartupBindingError("S3 bucket locator is invalid")


def _validate_s3_prefix(value: str) -> None:
    if (
        not isinstance(value, str)
        or _S3_PREFIX.fullmatch(value) is None
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise StartupBindingError("S3 prefix locator is invalid")


def _validate_aws_account(value: str, label: str) -> None:
    if not isinstance(value, str) or _AWS_ACCOUNT.fullmatch(value) is None:
        raise StartupBindingError(f"{label} is invalid")


def _validate_aws_region(value: str) -> None:
    if not isinstance(value, str) or len(value) > 32 or _AWS_REGION.fullmatch(value) is None:
        raise StartupBindingError("AWS region is invalid")


def _validate_sqs_queue_url(value: str, *, expected_account_id: str, region: str) -> None:
    if not isinstance(value, str) or len(value) > 512:
        raise StartupBindingError("SQS queue locator is invalid")
    parsed = urlsplit(value)
    parts = parsed.path.removeprefix("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != f"sqs.{region}.amazonaws.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or parts[0] != expected_account_id
        or _SQS_QUEUE_NAME.fullmatch(parts[1]) is None
    ):
        raise StartupBindingError("SQS queue locator does not match its owner and region")


def _require_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise StartupBindingError(f"{label} fields are invalid")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise StartupBindingError(f"{label} must be an object")
    return cast("Mapping[str, object]", value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise StartupBindingError(f"{label} must be a string")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


__all__ = [
    "RUN_STORE_STARTUP_BINDING_SCHEMA",
    "SCHEDULE_SOURCE_STARTUP_BINDING_SCHEMA",
    "PostgreSQLRunStoreStartupBinding",
    "PostgreSQLScheduleSourceStartupBinding",
    "RunStoreStartupBinding",
    "S3RunStoreStartupBinding",
    "SQSScheduleSourceStartupBinding",
    "ScheduleSourceStartupBinding",
    "StartupBindingError",
    "deserialize_run_store_startup_binding",
    "deserialize_schedule_source_startup_binding",
    "serialize_run_store_startup_binding",
    "serialize_schedule_source_startup_binding",
]
