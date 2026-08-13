"""Lazy OCI SDK adapters for lifecycle records and Container Instances."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast

from dander.providers.oci_container_instances.controller import (
    OciExecution,
    OciInstanceStatus,
    OciLifecycleError,
    StoredExecution,
    execution_from_json,
)

_ACTIVE_PREFIX = "active"
_HISTORY_PREFIX = "history"
_LOG_PREFIX = "logs"


@dataclass(frozen=True, slots=True)
class _Object:
    content: bytes
    etag: str


class OciObjectRunRepository:
    """Object Storage repository using conditional writes as the pipeline lock."""

    def __init__(
        self,
        *,
        client: object,
        namespace: str,
        bucket: str,
    ) -> None:
        self._client = cast("Any", client)
        self._namespace = namespace
        self._bucket = bucket

    def claim(self, execution: OciExecution) -> tuple[StoredExecution, bool]:
        historical = self._read(self._history_key(execution.pipeline_id, execution.run_id))
        if historical is not None:
            return StoredExecution(execution_from_json(historical.content), historical.etag), False
        key = self._active_key(execution.pipeline_id)
        try:
            response = self._put(key, execution, if_none_match="*")
        except Exception as error:  # noqa: BLE001 - SDK exception classes remain optional.
            if _status(error) != 412:
                raise OciLifecycleError("Could not claim OCI pipeline execution") from error
            active = self._read(key)
            if active is None:
                raise OciLifecycleError("OCI pipeline lock changed during claim") from error
            return StoredExecution(execution_from_json(active.content), active.etag), False
        return StoredExecution(execution, _etag(response)), True

    def get(self, pipeline_id: str, run_id: str | None = None) -> StoredExecution | None:
        active = self._read(self._active_key(pipeline_id))
        if active is not None:
            execution = execution_from_json(active.content)
            if run_id is None or execution.run_id == run_id:
                return StoredExecution(execution, active.etag)
        if run_id is None:
            return None
        historical = self._read(self._history_key(pipeline_id, run_id))
        if historical is None:
            return None
        return StoredExecution(execution_from_json(historical.content), historical.etag)

    def save(self, stored: StoredExecution, execution: OciExecution) -> StoredExecution:
        _require_same_run(stored.execution, execution)
        response = self._put(
            self._active_key(execution.pipeline_id),
            execution,
            if_match=stored.version,
        )
        return StoredExecution(execution, _etag(response))

    def finish(self, stored: StoredExecution, execution: OciExecution) -> StoredExecution:
        if not execution.terminal:
            raise OciLifecycleError("OCI repository can finish only terminal executions")
        active_key = self._active_key(execution.pipeline_id)
        active = self._read(active_key)
        history_key = self._history_key(execution.pipeline_id, execution.run_id)
        if active is None:
            historical = self._read(history_key)
            if historical is not None and execution_from_json(historical.content) == execution:
                return StoredExecution(execution, historical.etag)
            raise OciLifecycleError("OCI active execution disappeared before it was preserved")
        active_execution = execution_from_json(active.content)
        _require_same_run(active_execution, execution)
        current = self.save(StoredExecution(active_execution, active.etag), execution)
        try:
            response = self._put(history_key, execution, if_none_match="*")
            history_version = _etag(response)
        except Exception as error:  # noqa: BLE001 - an idempotent repeat may already exist.
            if _status(error) != 412:
                raise OciLifecycleError("Could not preserve OCI terminal execution") from error
            existing = self._read(history_key)
            if existing is None or execution_from_json(existing.content) != execution:
                raise OciLifecycleError("OCI terminal execution history conflicts") from error
            history_version = existing.etag
        try:
            self._client.delete_object(
                self._namespace,
                self._bucket,
                active_key,
                if_match=current.version,
            )
        except Exception as error:  # noqa: BLE001
            if _status(error) != 404:
                raise OciLifecycleError("Could not release OCI pipeline execution") from error
        return StoredExecution(execution, history_version)

    def save_logs(self, execution: OciExecution, content: bytes) -> None:
        if len(content) > 262_144:
            content = content[-262_144:]
        key = f"{_LOG_PREFIX}/{execution.pipeline_id}/{execution.run_id}/{execution.attempt}.log"
        try:
            self._client.put_object(
                self._namespace,
                self._bucket,
                key,
                content,
                content_type="text/plain; charset=utf-8",
                if_none_match="*",
            )
        except Exception as error:  # noqa: BLE001
            if _status(error) != 412:
                raise OciLifecycleError("Could not preserve bounded OCI runtime logs") from error

    def get_logs(self, execution: OciExecution, *, attempt: int | None = None) -> bytes:
        selected = attempt or execution.attempt
        result = self._read(
            f"{_LOG_PREFIX}/{execution.pipeline_id}/{execution.run_id}/{selected}.log"
        )
        return b"" if result is None else result.content

    def get_projection(self, key: str) -> dict[str, object]:
        """Read one Terraform-owned non-secret projection document."""
        if not key.startswith("projections/") or not key.endswith(".json"):
            raise OciLifecycleError("OCI projection object key is invalid")
        result = self._read(key)
        if result is None or len(result.content) > 131_072:
            raise OciLifecycleError("OCI execution projection was not found")
        try:
            document = json.loads(result.content)
        except json.JSONDecodeError as error:
            raise OciLifecycleError("OCI execution projection is invalid") from error
        if not isinstance(document, dict):
            raise OciLifecycleError("OCI execution projection is invalid")
        return cast("dict[str, object]", document)

    def _put(
        self,
        key: str,
        execution: OciExecution,
        **conditions: str,
    ) -> object:
        content = json.dumps(execution.as_dict(), sort_keys=True, separators=(",", ":")).encode()
        return self._client.put_object(
            self._namespace,
            self._bucket,
            key,
            content,
            content_type="application/json",
            **conditions,
        )

    def _read(self, key: str) -> _Object | None:
        try:
            response = self._client.get_object(self._namespace, self._bucket, key)
        except Exception as error:  # noqa: BLE001
            if _status(error) == 404:
                return None
            raise OciLifecycleError("Could not read OCI execution record") from error
        data = getattr(response, "data", None)
        content = getattr(data, "content", None)
        if not isinstance(content, bytes):
            raw = getattr(data, "raw", None)
            content = raw.read() if raw is not None else None
        if not isinstance(content, bytes):
            raise OciLifecycleError("OCI execution record body is invalid")
        return _Object(content=content, etag=_etag(response))

    @staticmethod
    def _active_key(pipeline_id: str) -> str:
        return f"{_ACTIVE_PREFIX}/{pipeline_id}.json"

    @staticmethod
    def _history_key(pipeline_id: str, run_id: str) -> str:
        return f"{_HISTORY_PREFIX}/{pipeline_id}/{run_id}.json"


class OciSdkContainerGateway:
    """Create run-scoped instances and normalize lifecycle state through the OCI SDK."""

    def __init__(self, *, client: object) -> None:
        self._client = cast("Any", client)

    def create(self, projection: Mapping[str, object], execution: OciExecution) -> str:
        try:
            oci = cast("Any", import_module("oci"))
        except ImportError as error:  # pragma: no cover - packaged OCI extra covers this path.
            raise OciLifecycleError("OCI SDK is unavailable") from error
        resources = _mapping(projection, "resources")
        network = _mapping(projection, "network")
        extensions = _mapping(projection, "extensions")
        environment = {str(k): str(v) for k, v in _mapping(projection, "environment").items()}
        environment.update(
            {
                "DANDER_RUN_ID": execution.run_id,
                "DANDER_LAUNCHER_EXECUTION_ID": execution.run_id,
                "DANDER_ATTEMPT": str(execution.attempt),
                "DANDER_SHARD_INDEX": "0",
                "DANDER_SHARD_COUNT": "1",
                "DANDER_DEADLINE_AT": execution.deadline_at,
                "DANDER_SECRET_BINDINGS_JSON": json.dumps(
                    _mapping(projection, "secret_bindings"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        cpu = cast("int", resources["cpu_millis"]) / 1_000
        memory = cast("int", resources["memory_mib"]) / 1_024
        freeform_tags = {
            "managed-by": "dander",
            "dander-run-id": execution.run_id,
            "dander-pipeline": execution.pipeline_id,
            "dander-attempt": str(execution.attempt),
        }
        details = oci.container_instances.models.CreateContainerInstanceDetails(
            compartment_id=str(extensions["oci_compartment_id"]),
            availability_domain=str(_mapping(network, "extensions")["oci_availability_domain"]),
            display_name=f"dander-{execution.pipeline_id}-{execution.run_id[-8:]}-a{execution.attempt}",
            shape=str(extensions["oci_shape"]),
            shape_config=oci.container_instances.models.CreateContainerInstanceShapeConfigDetails(
                ocpus=cpu,
                memory_in_gbs=memory,
            ),
            container_restart_policy="NEVER",
            graceful_shutdown_timeout_in_seconds=int(
                cast("str", extensions["oci_graceful_shutdown_seconds"])
            ),
            vnics=[
                oci.container_instances.models.CreateContainerVnicDetails(
                    subnet_id=str(network["placement"]),
                    is_public_ip_assigned=False,
                )
            ],
            volumes=[
                oci.container_instances.models.CreateContainerEmptyDirVolumeDetails(
                    name="dander-tmp",
                    volume_type="EMPTYDIR",
                    backing_store="EPHEMERAL_STORAGE",
                )
            ],
            containers=[
                oci.container_instances.models.CreateContainerDetails(
                    display_name="runtime",
                    image_url=str(projection["image"]),
                    arguments=[str(item) for item in _sequence(projection, "command")],
                    environment_variables=environment,
                    is_resource_principal_disabled=False,
                    resource_config=oci.container_instances.models.CreateContainerResourceConfigDetails(
                        vcpus_limit=cpu,
                        memory_limit_in_gbs=memory,
                    ),
                    volume_mounts=[
                        oci.container_instances.models.CreateVolumeMountDetails(
                            mount_path="/tmp",
                            volume_name="dander-tmp",
                            is_read_only=False,
                        )
                    ],
                    security_context=oci.container_instances.models.CreateLinuxSecurityContextDetails(
                        run_as_user=65532,
                        run_as_group=65532,
                        is_non_root_user_check_enabled=True,
                        is_root_file_system_readonly=True,
                    ),
                    freeform_tags=freeform_tags,
                )
            ],
            # OCI rejects the entire request unless every container's tags are null or exactly
            # equal to its parent Container Instance's tags.
            freeform_tags=freeform_tags,
        )
        response = self._client.create_container_instance(
            details,
            opc_retry_token=f"{execution.run_id}-a{execution.attempt}",
        )
        instance_id = getattr(getattr(response, "data", None), "id", None)
        if not isinstance(instance_id, str) or not instance_id.startswith(
            "ocid1.computecontainerinstance"
        ):
            raise OciLifecycleError("OCI did not return a Container Instance OCID")
        return instance_id

    def status(self, instance_id: str) -> OciInstanceStatus:
        response = self._client.get_container_instance(instance_id)
        instance = getattr(response, "data", None)
        lifecycle = str(getattr(instance, "lifecycle_state", "")).upper()
        containers = getattr(instance, "containers", None) or []
        if containers:
            container = containers[0]
            container_id = getattr(container, "id", None)
            container_state = str(getattr(container, "lifecycle_state", "")).upper()
            exit_code = getattr(container, "exit_code", None)
            if container_state in {"TERMINATED", "FAILED"}:
                state = "succeeded" if exit_code == 0 else "failed"
                return OciInstanceStatus(
                    state=cast("Any", state),
                    container_id=container_id if isinstance(container_id, str) else None,
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                    failure_code=(None if exit_code in {0, 75} else "runtime_failed"),
                )
            return OciInstanceStatus(
                state="running" if container_state == "ACTIVE" else "pending",
                container_id=container_id if isinstance(container_id, str) else None,
            )
        if lifecycle == "FAILED":
            return OciInstanceStatus(state="failed", failure_code="container_instance_failed")
        return OciInstanceStatus(state="running" if lifecycle == "ACTIVE" else "pending")

    def stop(self, instance_id: str) -> None:
        try:
            self._client.stop_container_instance(instance_id)
        except Exception as error:  # noqa: BLE001
            if _status(error) not in {404, 409}:
                raise

    def delete(self, instance_id: str) -> None:
        try:
            self._client.delete_container_instance(instance_id)
        except Exception as error:  # noqa: BLE001
            if _status(error) != 404:
                raise

    def logs(self, container_id: str, *, limit_bytes: int) -> bytes:
        response = self._client.retrieve_logs(container_id)
        data = getattr(response, "data", None)
        content = getattr(data, "content", None)
        if not isinstance(content, bytes):
            raw = getattr(data, "raw", None)
            content = raw.read() if raw is not None else b""
        return bytes(content)[-limit_bytes:]


def build_resource_principal_adapters(
    *,
    namespace: str,
    bucket: str,
) -> tuple[OciObjectRunRepository, OciSdkContainerGateway]:
    """Build controller adapters without credential files or static cloud keys."""
    try:
        oci = cast("Any", import_module("oci"))
    except ImportError as error:  # pragma: no cover
        raise OciLifecycleError("OCI SDK is unavailable") from error
    signer = oci.auth.signers.get_resource_principals_signer()
    return (
        OciObjectRunRepository(
            client=oci.object_storage.ObjectStorageClient(config={}, signer=signer),
            namespace=namespace,
            bucket=bucket,
        ),
        OciSdkContainerGateway(
            client=oci.container_instances.ContainerInstanceClient(config={}, signer=signer)
        ),
    )


def _etag(response: object) -> str:
    headers = getattr(response, "headers", None)
    value = headers.get("etag") if isinstance(headers, Mapping) else None
    if not isinstance(value, str) or not value:
        raise OciLifecycleError("OCI Object Storage response omitted an ETag")
    return value


def _status(error: Exception) -> int | None:
    value = getattr(error, "status", None)
    return value if isinstance(value, int) else None


def _require_same_run(existing: OciExecution, updated: OciExecution) -> None:
    if (existing.pipeline_id, existing.run_id) != (updated.pipeline_id, updated.run_id):
        raise OciLifecycleError("OCI execution update changed its identity")


def _mapping(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise OciLifecycleError(f"OCI execution projection {key} is invalid")
    return value


def _sequence(document: Mapping[str, object], key: str) -> list[object]:
    value = document.get(key)
    if not isinstance(value, list):
        raise OciLifecycleError(f"OCI execution projection {key} is invalid")
    return value


__all__ = [
    "OciObjectRunRepository",
    "OciSdkContainerGateway",
    "build_resource_principal_adapters",
]
