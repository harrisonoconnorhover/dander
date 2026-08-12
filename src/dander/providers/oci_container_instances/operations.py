"""Manifest-bound OCI Container Instances lifecycle operations."""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from dander.project import ProjectConfigError, load_project_config
from dander.providers.oci_container_instances.controller import (
    OciExecution,
    OciLifecycleError,
    execution_from_json,
    execution_run_id,
)
from dander.providers.oci_container_instances.oci_adapter import OciObjectRunRepository

if TYPE_CHECKING:
    from collections.abc import Callable

_FUNCTION_OCID = re.compile(r"^ocid1\.fnfunc\.oc[0-9]+\.[a-z0-9-]+\.[A-Za-z0-9]+$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PIPELINE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class OciOperationError(RuntimeError):
    """An OCI operation was unbound, rejected, or returned invalid data."""


@dataclass(frozen=True, slots=True)
class OciOperationBinding:
    """Exact project pipeline to OCI Function and run-record binding."""

    region: str
    namespace: str
    bucket: str
    pipeline_id: str
    function_id: str
    project_dir: Path

    @classmethod
    def from_project(
        cls,
        *,
        config: Path,
        deployment: str,
        pipeline_id: str,
        function_id: str,
        name: str = "dander",
    ) -> OciOperationBinding:
        if _PIPELINE.fullmatch(pipeline_id) is None:
            raise OciOperationError("Invalid OCI pipeline identifier")
        if _FUNCTION_OCID.fullmatch(function_id) is None:
            raise OciOperationError("Invalid OCI Function OCID")
        resolved = config.expanduser().resolve()
        try:
            manifest = load_project_config(resolved, deployment=deployment)
            if manifest.launcher_provider != "oci_container_instances":
                raise ProjectConfigError(
                    f"Deployment {deployment!r} does not select "
                    "launcher.provider='oci_container_instances'"
                )
            manifest.validate_references(resolved.parent)
            manifest.pipelines[pipeline_id]
            launcher = manifest.resolved_launcher_config()
        except KeyError as error:
            raise OciOperationError(
                f"Pipeline {pipeline_id!r} is not declared in the project manifest"
            ) from error
        except ProjectConfigError as error:
            raise OciOperationError(str(error)) from error
        namespace = launcher.get("registry_namespace")
        compartment_id = launcher.get("compartment_id")
        region = launcher.get("region")
        if not all(isinstance(value, str) for value in (namespace, compartment_id, region)):
            raise OciOperationError("OCI launcher binding is incomplete")
        compartment = cast("str", compartment_id)
        suffix = hashlib.sha256(compartment.encode()).hexdigest()[:8]
        return cls(
            region=cast("str", region),
            namespace=cast("str", namespace),
            bucket=f"{name}-oci-runs-{suffix}",
            pipeline_id=pipeline_id,
            function_id=function_id,
            project_dir=resolved.parent,
        )


@dataclass(frozen=True, slots=True)
class OciInvocation:
    """Sanitized acknowledgement for a detached lifecycle Function invocation."""

    action: str
    run_id: str
    accepted: bool = True

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class _FunctionInvoker:
    def __init__(
        self,
        *,
        management_client: object,
        signer: object,
        config: dict[str, Any],
        expected_pipeline: str,
    ) -> None:
        self._management = cast("Any", management_client)
        self._signer = signer
        self._config = config
        self._expected_pipeline = expected_pipeline

    def invoke(self, function_id: str, payload: dict[str, object], *, detached: bool) -> bytes:
        try:
            oci = cast("Any", import_module("oci"))
        except ImportError as error:  # pragma: no cover
            raise OciOperationError("OCI SDK is unavailable") from error
        try:
            function = getattr(self._management.get_function(function_id), "data", None)
        except Exception as error:  # noqa: BLE001 - OCI SDK errors vary by release.
            raise OciOperationError("OCI Function metadata could not be read") from error
        function_config = getattr(function, "config", None)
        if (
            not isinstance(function_config, dict)
            or function_config.get("DANDER_OCI_PIPELINE") != self._expected_pipeline
        ):
            raise OciOperationError("OCI Function does not match the selected pipeline")
        endpoint = getattr(function, "invoke_endpoint", None)
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise OciOperationError("OCI Function invoke endpoint is unavailable")
        client = oci.functions.FunctionsInvokeClient(
            self._config,
            signer=self._signer,
            service_endpoint=endpoint,
        )
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            response = client.invoke_function(
                function_id,
                invoke_function_body=io.BytesIO(body),
                fn_invoke_type="detached" if detached else "sync",
            )
        except Exception as error:  # noqa: BLE001 - OCI SDK errors vary by release.
            raise OciOperationError("OCI Function invocation failed") from error
        if detached:
            return b""
        data = getattr(response, "data", None)
        content = getattr(data, "content", None)
        if isinstance(content, bytes):
            return content
        raw = getattr(data, "raw", None)
        result = raw.read() if raw is not None else b""
        return result if isinstance(result, bytes) else b""


class OciContainerInstanceOperations:
    """Start, observe, cancel, replay, and read bounded logs for one OCI pipeline."""

    def __init__(
        self,
        binding: OciOperationBinding,
        *,
        repository: OciObjectRunRepository,
        invoker: object,
        clock: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        self.binding = binding
        self._repository = repository
        self._invoker = cast("Any", invoker)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._nonce = nonce or (lambda: uuid4().hex[:12])

    @classmethod
    def from_security_token_profile(
        cls,
        binding: OciOperationBinding,
        *,
        profile: str = "DEFAULT",
    ) -> OciContainerInstanceOperations:
        """Build SDK clients from one expiring OCI session profile, never an API key signer."""
        if _PROFILE.fullmatch(profile) is None:
            raise OciOperationError("Invalid OCI SecurityToken profile")
        try:
            oci = cast("Any", import_module("oci"))
        except ImportError as error:  # pragma: no cover
            raise OciOperationError("OCI SDK is unavailable") from error
        try:
            config = oci.config.from_file(profile_name=profile)
            token_path = config.get("security_token_file")
            key_path = config.get("key_file")
            if not isinstance(token_path, str) or not isinstance(key_path, str):
                raise OciOperationError(
                    "OCI profile must be an authenticated SecurityToken session"
                )
            token = Path(token_path).expanduser().read_text(encoding="utf-8").strip()
            private_key = oci.signer.load_private_key_from_file(
                Path(key_path).expanduser(),
                pass_phrase=config.get("pass_phrase"),
            )
            signer = oci.auth.signers.SecurityTokenSigner(token, private_key)
        except (OSError, ValueError) as error:
            raise OciOperationError("OCI SecurityToken session could not be loaded") from error
        config["region"] = binding.region
        repository = OciObjectRunRepository(
            client=oci.object_storage.ObjectStorageClient(config, signer=signer),
            namespace=binding.namespace,
            bucket=binding.bucket,
        )
        return cls(
            binding,
            repository=repository,
            invoker=_FunctionInvoker(
                management_client=oci.functions.FunctionsManagementClient(config, signer=signer),
                signer=signer,
                config=config,
                expected_pipeline=binding.pipeline_id,
            ),
        )

    def start(self) -> OciInvocation:
        """Queue one detached manual execution with a caller-known stable run id."""
        key = self._new_key("manual")
        self._invoker.invoke(
            self.binding.function_id,
            {"action": "start", "idempotency_key": key},
            detached=True,
        )
        return OciInvocation(
            action="start",
            run_id=execution_run_id(self.binding.pipeline_id, key),
        )

    def latest(self) -> OciExecution | None:
        """Return the active execution, if any."""
        stored = self._repository.get(self.binding.pipeline_id)
        return None if stored is None else stored.execution

    def describe(self, run_id: str) -> OciExecution:
        """Return one exact active or terminal execution."""
        stored = self._repository.get(self.binding.pipeline_id, run_id)
        if stored is None:
            raise OciOperationError("OCI execution was not found")
        return stored.execution

    def logs(self, run_id: str, *, attempt: int | None = None) -> bytes:
        """Return at most 256 KiB of runtime output retained outside Git."""
        execution = self.describe(run_id)
        return self._repository.get_logs(execution, attempt=attempt)

    def cancel(self, run_id: str) -> OciExecution:
        """Synchronously request interruption and return the controller record."""
        content = self._invoker.invoke(
            self.binding.function_id,
            {"action": "cancel", "run_id": run_id},
            detached=False,
        )
        return _execution_response(content)

    def replay(self, run_id: str) -> OciInvocation:
        """Queue a fresh attempt chain for one terminal execution."""
        previous = self.describe(run_id)
        if not previous.terminal:
            raise OciOperationError("Only a terminal OCI execution can be replayed")
        key = self._new_key("replay")
        self._invoker.invoke(
            self.binding.function_id,
            {"action": "replay", "run_id": run_id, "idempotency_key": key},
            detached=True,
        )
        return OciInvocation(
            action="replay",
            run_id=execution_run_id(self.binding.pipeline_id, key),
        )

    def _new_key(self, prefix: str) -> str:
        now = self._clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{prefix}:{self.binding.pipeline_id}:{now}:{self._nonce()}"


def _execution_response(content: bytes) -> OciExecution:
    try:
        document = json.loads(content)
    except json.JSONDecodeError as error:
        raise OciOperationError("OCI Function returned an invalid response") from error
    if not isinstance(document, dict):
        raise OciOperationError("OCI Function returned an invalid response")
    try:
        return execution_from_json(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        )
    except OciLifecycleError as error:
        raise OciOperationError(str(error)) from error


__all__ = [
    "OciContainerInstanceOperations",
    "OciInvocation",
    "OciOperationBinding",
    "OciOperationError",
]
