"""Saved-plan OCI stage-zero and foundation Terraform lifecycles."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

from dander.bootstrap.terraform import (
    TerraformBootstrapError,
    build_launcher_runtime,
    validate_runtime_settings,
    validate_terraform_pipelines,
)
from dander.deployment import ExecutionProjectionError, ResolvedTemplateRequest
from dander.providers import ProviderFactoryError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[1-9][0-9]*$")
_TENANCY_OCID = re.compile(r"^ocid1\.tenancy\.oc[0-9]+\.\.[A-Za-z0-9]+$")
_COMPARTMENT_OCID = re.compile(r"^ocid1\.compartment\.oc[0-9]+\.\.[A-Za-z0-9]+$")
_BUCKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,254}$")
_REPOSITORY = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_DYNAMIC_GROUP = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_STATE_KEY = re.compile(r"^(?!/)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_CONTROLLER_IMAGE = re.compile(r"^[a-z0-9.-]+/[a-z0-9_-]+/[a-z0-9._/-]+:[A-Za-z0-9._-]+$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_BACKEND_SCHEMA = "io.dander.oci-bootstrap-backend/v1"


class OciTerraformBootstrapError(RuntimeError):
    """Raised when an OCI saved-plan lifecycle cannot complete safely."""


def build_oci_execution_projections(
    *,
    container_image: str,
    launcher_config: Mapping[str, object],
    profile_id: str,
    runtime_cpu: int,
    runtime_memory: str,
    runtime_timeout_seconds: int,
    runtime_max_retries: int,
    runtime_batch_rows: int,
    require_guarded_free_tier: bool,
    pipelines: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    """Build validated OCI templates without contacting OCI."""
    expanded_pipelines = dict(pipelines)
    if not expanded_pipelines:
        raise OciTerraformBootstrapError("OCI launcher planning requires at least one pipeline")
    try:
        validate_runtime_settings(
            cpu=runtime_cpu,
            memory=runtime_memory,
            timeout_seconds=runtime_timeout_seconds,
            max_retries=runtime_max_retries,
            batch_rows=runtime_batch_rows,
            require_guarded_free_tier=require_guarded_free_tier,
        )
        validate_terraform_pipelines(expanded_pipelines)
        launcher = build_launcher_runtime(
            launcher_config=dict(launcher_config),
            gcp_context=None,
        )
        return {
            pipeline_id: template.as_dict()
            for pipeline_id, template in launcher.templates.build(
                ResolvedTemplateRequest(
                    pipelines=expanded_pipelines,
                    image=container_image,
                    profile_id=profile_id,
                    cpu=runtime_cpu,
                    memory=runtime_memory,
                    deadline_seconds=runtime_timeout_seconds,
                    launcher_retry_count=runtime_max_retries,
                    batch_rows=runtime_batch_rows,
                    alert_target="oci_notifications",
                )
            ).items()
        }
    except (ExecutionProjectionError, ProviderFactoryError, TerraformBootstrapError) as error:
        raise OciTerraformBootstrapError(str(error)) from error


class OciAdministrativeBootstrap:
    """Create OCI state and registry prerequisites, then migrate to native remote state."""

    def __init__(self, infra_dir: Path, operator_artifact_dir: Path) -> None:
        self._workspace = _OciWorkspace(
            infra_dir=infra_dir,
            operator_artifact_dir=operator_artifact_dir,
            plan_name="dander-oci-admin-bootstrap.tfplan",
        )
        self._backend_path = self._workspace.workspace / "backend.tf.json"
        self._backend_record_path = self._workspace.operator_dir / "backend.json"
        self._local_state_path = self._workspace.operator_dir / "terraform.tfstate"

    def execute(
        self,
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        state_bucket_name: str,
        state_key: str,
        repository_name: str,
        config_file_profile: str = "DEFAULT",
    ) -> Path:
        """Save one stage-zero plan without creating OCI resources."""
        self._validate(
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            repository_name=repository_name,
            config_file_profile=config_file_profile,
        )
        self._workspace.prepare()
        backend = self._load_backend_record()
        if backend is None:
            self._write_backend("local", {"path": str(self._local_state_path)})
        else:
            self._require_matching_backend(
                backend,
                tenancy_id=tenancy_id,
                region=region,
                namespace=str(backend.get("namespace", "")),
                state_bucket_name=state_bucket_name,
                state_key=state_key,
                config_file_profile=config_file_profile,
            )
            self._write_oci_backend(backend)
        self._workspace.run("terraform", "init", "-reconfigure", "-input=false")
        self._workspace.run(
            "terraform",
            "plan",
            "-input=false",
            f"-var=tenancy_id={tenancy_id}",
            f"-var=compartment_id={compartment_id}",
            f"-var=region={region}",
            f"-var=config_file_profile={config_file_profile}",
            f"-var=state_bucket_name={state_bucket_name}",
            f"-var=repository_name={repository_name}",
            f"-out={self._workspace.plan_path}",
        )
        self._workspace.secure_plan()
        return self._workspace.plan_path

    def apply_saved_plan(
        self,
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        repository_name: str,
        config_file_profile: str = "DEFAULT",
    ) -> Path:
        """Apply only the reviewed plan, then migrate its local state to Object Storage."""
        self._validate(
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            repository_name=repository_name,
            config_file_profile=config_file_profile,
        )
        _require(_NAMESPACE, namespace, "OCI Object Storage namespace")
        self._workspace.prepare(preserve_plan=True)
        self._workspace.require_plan()
        backend = self._load_backend_record()
        if backend is None:
            self._write_backend("local", {"path": str(self._local_state_path)})
        else:
            self._require_matching_backend(
                backend,
                tenancy_id=tenancy_id,
                region=region,
                namespace=namespace,
                state_bucket_name=state_bucket_name,
                state_key=state_key,
                config_file_profile=config_file_profile,
            )
            self._write_oci_backend(backend)
        self._workspace.run("terraform", "init", "-reconfigure", "-input=false")
        self._workspace.run("terraform", "apply", "-input=false", str(self._workspace.plan_path))
        if backend is None:
            record: dict[str, object] = {
                "schema": _BACKEND_SCHEMA,
                "tenancy_id": tenancy_id,
                "region": region,
                "namespace": namespace,
                "bucket": state_bucket_name,
                "key": state_key,
                "auth": "SecurityToken",
                "config_file_profile": config_file_profile,
            }
            self._write_oci_backend(record)
            try:
                self._workspace.run(
                    "terraform",
                    "init",
                    "-migrate-state",
                    "-force-copy",
                    "-input=false",
                )
            except OciTerraformBootstrapError:
                self._write_backend("local", {"path": str(self._local_state_path)})
                raise
            _write_json(self._backend_record_path, record)
        return self._workspace.plan_path

    def verify_no_drift(
        self,
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        repository_name: str,
        config_file_profile: str = "DEFAULT",
    ) -> None:
        """Refresh the migrated stage-zero state and fail if configuration would change."""
        self._validate(
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            repository_name=repository_name,
            config_file_profile=config_file_profile,
        )
        _require(_NAMESPACE, namespace, "OCI Object Storage namespace")
        self._workspace.prepare(preserve_plan=True)
        backend = self._load_backend_record()
        if backend is None:
            raise OciTerraformBootstrapError(
                "OCI stage-zero verification requires migrated remote state"
            )
        self._require_matching_backend(
            backend,
            tenancy_id=tenancy_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            config_file_profile=config_file_profile,
        )
        self._write_oci_backend(backend)
        self._workspace.run("terraform", "init", "-reconfigure", "-input=false")
        self._workspace.verify_no_drift(
            "terraform",
            "plan",
            "-input=false",
            "-detailed-exitcode",
            f"-var=tenancy_id={tenancy_id}",
            f"-var=compartment_id={compartment_id}",
            f"-var=region={region}",
            f"-var=config_file_profile={config_file_profile}",
            f"-var=state_bucket_name={state_bucket_name}",
            f"-var=repository_name={repository_name}",
        )

    def _load_backend_record(self) -> dict[str, object] | None:
        if not self._backend_record_path.exists():
            return None
        if self._backend_record_path.is_symlink() or not self._backend_record_path.is_file():
            raise OciTerraformBootstrapError("OCI backend record must be a regular file")
        try:
            document = json.loads(self._backend_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OciTerraformBootstrapError("OCI backend record is unreadable") from error
        if not isinstance(document, dict) or document.get("schema") != _BACKEND_SCHEMA:
            raise OciTerraformBootstrapError("OCI backend record has an unsupported schema")
        return document

    @staticmethod
    def _require_matching_backend(
        backend: dict[str, object],
        *,
        tenancy_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        config_file_profile: str,
    ) -> None:
        expected = {
            "tenancy_id": tenancy_id,
            "region": region,
            "namespace": namespace,
            "bucket": state_bucket_name,
            "key": state_key,
            "auth": "SecurityToken",
            "config_file_profile": config_file_profile,
        }
        if any(backend.get(key) != value for key, value in expected.items()):
            raise OciTerraformBootstrapError(
                "OCI backend inputs do not match the previously migrated stage-zero state"
            )

    def _write_oci_backend(self, backend: Mapping[str, object]) -> None:
        self._write_backend(
            "oci",
            {
                "tenancy_ocid": backend["tenancy_id"],
                "region": backend["region"],
                "namespace": backend["namespace"],
                "bucket": backend["bucket"],
                "key": backend["key"],
                "auth": "SecurityToken",
                "config_file_profile": backend["config_file_profile"],
            },
        )

    def _write_backend(self, backend_type: str, config: dict[str, object]) -> None:
        _write_json(
            self._backend_path,
            {"terraform": {"backend": {backend_type: config}}},
        )

    @staticmethod
    def _validate(
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        state_bucket_name: str,
        state_key: str,
        repository_name: str,
        config_file_profile: str,
    ) -> None:
        for pattern, value, label in (
            (_TENANCY_OCID, tenancy_id, "OCI tenancy"),
            (_COMPARTMENT_OCID, compartment_id, "OCI compartment"),
            (_REGION, region, "OCI region"),
            (_BUCKET, state_bucket_name, "OCI state bucket"),
            (_STATE_KEY, state_key, "OCI state key"),
            (_REPOSITORY, repository_name, "OCIR repository"),
            (_PROFILE, config_file_profile, "OCI SecurityToken profile"),
        ):
            _require(pattern, value, label)


class OciTerraformBootstrap:
    """Plan or apply the OCI network, Vault, identity, and observability foundation."""

    def __init__(self, infra_dir: Path, operator_artifact_dir: Path) -> None:
        self._workspace = _OciWorkspace(
            infra_dir=infra_dir,
            operator_artifact_dir=operator_artifact_dir,
            plan_name="dander-oci-foundation.tfplan",
        )

    def execute(
        self,
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        dynamic_group_name: str,
        config_file_profile: str = "DEFAULT",
        name: str = "dander",
        controller_image: str | None = None,
        controller_image_digest: str | None = None,
        execution_projections: Mapping[str, object] | None = None,
        controller_dynamic_group_name: str = "dander_phase7_controller",
        scheduler_dynamic_group_name: str = "dander_phase7_scheduler",
    ) -> Path:
        """Produce one immutable remote-state-backed OCI foundation plan."""
        self._validate(
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            dynamic_group_name=dynamic_group_name,
            config_file_profile=config_file_profile,
            name=name,
        )
        controller_args = self._controller_args(
            controller_image=controller_image,
            controller_image_digest=controller_image_digest,
            execution_projections=execution_projections,
            controller_dynamic_group_name=controller_dynamic_group_name,
            scheduler_dynamic_group_name=scheduler_dynamic_group_name,
        )
        self._workspace.prepare()
        self._init(
            tenancy_id=tenancy_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            config_file_profile=config_file_profile,
        )
        self._workspace.run(
            "terraform",
            "plan",
            "-input=false",
            f"-var=tenancy_id={tenancy_id}",
            f"-var=compartment_id={compartment_id}",
            f"-var=region={region}",
            f"-var=config_file_profile={config_file_profile}",
            f"-var=object_storage_namespace={namespace}",
            f"-var=dynamic_group_name={dynamic_group_name}",
            f"-var=name={name}",
            *controller_args,
            f"-out={self._workspace.plan_path}",
        )
        self._workspace.secure_plan()
        return self._workspace.plan_path

    def apply_saved_plan(
        self,
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        dynamic_group_name: str,
        config_file_profile: str = "DEFAULT",
        name: str = "dander",
    ) -> Path:
        """Apply only the reviewed OCI foundation plan."""
        self._validate(
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            dynamic_group_name=dynamic_group_name,
            config_file_profile=config_file_profile,
            name=name,
        )
        self._workspace.prepare(preserve_plan=True)
        self._workspace.require_plan()
        self._init(
            tenancy_id=tenancy_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            config_file_profile=config_file_profile,
        )
        self._workspace.run("terraform", "apply", "-input=false", str(self._workspace.plan_path))
        return self._workspace.plan_path

    def verify_no_drift(
        self,
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        dynamic_group_name: str,
        config_file_profile: str = "DEFAULT",
        name: str = "dander",
        controller_image: str | None = None,
        controller_image_digest: str | None = None,
        execution_projections: Mapping[str, object] | None = None,
        controller_dynamic_group_name: str = "dander_phase7_controller",
        scheduler_dynamic_group_name: str = "dander_phase7_scheduler",
    ) -> None:
        """Refresh the OCI foundation and fail if its reviewed configuration would change."""
        self._validate(
            tenancy_id=tenancy_id,
            compartment_id=compartment_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            dynamic_group_name=dynamic_group_name,
            config_file_profile=config_file_profile,
            name=name,
        )
        controller_args = self._controller_args(
            controller_image=controller_image,
            controller_image_digest=controller_image_digest,
            execution_projections=execution_projections,
            controller_dynamic_group_name=controller_dynamic_group_name,
            scheduler_dynamic_group_name=scheduler_dynamic_group_name,
        )
        self._workspace.prepare(preserve_plan=True)
        self._init(
            tenancy_id=tenancy_id,
            region=region,
            namespace=namespace,
            state_bucket_name=state_bucket_name,
            state_key=state_key,
            config_file_profile=config_file_profile,
        )
        self._workspace.verify_no_drift(
            "terraform",
            "plan",
            "-input=false",
            "-detailed-exitcode",
            f"-var=tenancy_id={tenancy_id}",
            f"-var=compartment_id={compartment_id}",
            f"-var=region={region}",
            f"-var=config_file_profile={config_file_profile}",
            f"-var=object_storage_namespace={namespace}",
            f"-var=dynamic_group_name={dynamic_group_name}",
            f"-var=name={name}",
            *controller_args,
        )

    def _init(
        self,
        *,
        tenancy_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        config_file_profile: str,
    ) -> None:
        self._workspace.run(
            "terraform",
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=tenancy_ocid={tenancy_id}",
            f"-backend-config=region={region}",
            f"-backend-config=namespace={namespace}",
            f"-backend-config=bucket={state_bucket_name}",
            f"-backend-config=key={state_key}",
            "-backend-config=auth=SecurityToken",
            f"-backend-config=config_file_profile={config_file_profile}",
        )

    @staticmethod
    def _controller_args(
        *,
        controller_image: str | None,
        controller_image_digest: str | None,
        execution_projections: Mapping[str, object] | None,
        controller_dynamic_group_name: str,
        scheduler_dynamic_group_name: str,
    ) -> tuple[str, ...]:
        projections = dict(execution_projections or {})
        supplied = (
            controller_image is not None,
            controller_image_digest is not None,
            bool(projections),
        )
        if any(supplied) and not all(supplied):
            raise OciTerraformBootstrapError(
                "OCI controller image, digest, and execution projections must be supplied together"
            )
        for value, label in (
            (controller_dynamic_group_name, "OCI controller dynamic group"),
            (scheduler_dynamic_group_name, "OCI scheduler dynamic group"),
        ):
            _require(_DYNAMIC_GROUP, value, label)
        if not all(supplied):
            return ()
        assert controller_image is not None and controller_image_digest is not None
        _require(_CONTROLLER_IMAGE, controller_image, "OCI controller image")
        _require(_IMAGE_DIGEST, controller_image_digest, "OCI controller image digest")
        try:
            projections_json = json.dumps(
                projections,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise OciTerraformBootstrapError(
                "OCI execution projections are not JSON serializable"
            ) from error
        return (
            f"-var=controller_image={controller_image}",
            f"-var=controller_image_digest={controller_image_digest}",
            f"-var=controller_dynamic_group_name={controller_dynamic_group_name}",
            f"-var=scheduler_dynamic_group_name={scheduler_dynamic_group_name}",
            f"-var=execution_projections={projections_json}",
        )

    @staticmethod
    def _validate(
        *,
        tenancy_id: str,
        compartment_id: str,
        region: str,
        namespace: str,
        state_bucket_name: str,
        state_key: str,
        dynamic_group_name: str,
        config_file_profile: str,
        name: str,
    ) -> None:
        for pattern, value, label in (
            (_TENANCY_OCID, tenancy_id, "OCI tenancy"),
            (_COMPARTMENT_OCID, compartment_id, "OCI compartment"),
            (_REGION, region, "OCI region"),
            (_NAMESPACE, namespace, "OCI Object Storage namespace"),
            (_BUCKET, state_bucket_name, "OCI state bucket"),
            (_STATE_KEY, state_key, "OCI state key"),
            (_DYNAMIC_GROUP, dynamic_group_name, "OCI dynamic group"),
            (_PROFILE, config_file_profile, "OCI SecurityToken profile"),
            (_NAME, name, "OCI deployment name"),
        ):
            _require(pattern, value, label)


class _OciWorkspace:
    """Private Terraform workspace shared by OCI saved-plan lifecycles."""

    def __init__(self, *, infra_dir: Path, operator_artifact_dir: Path, plan_name: str) -> None:
        self.infra_dir = infra_dir.resolve()
        self.repository_dir = _repository_root(self.infra_dir)
        self.operator_dir = operator_artifact_dir.expanduser().resolve()
        if (
            self.operator_dir == self.repository_dir
            or self.repository_dir in self.operator_dir.parents
        ):
            raise OciTerraformBootstrapError(
                "OCI operator artifact directory must be outside the repository checkout"
            )
        self.workspace = self.operator_dir / "terraform-workspace"
        self.tf_data_dir = self.operator_dir / "terraform-data"
        self.plan_path = self.operator_dir / plan_name

    def prepare(self, *, preserve_plan: bool = False) -> None:
        try:
            for path, label in (
                (self.operator_dir, "operator artifact directory"),
                (self.workspace, "Terraform workspace"),
                (self.tf_data_dir, "terraform-data"),
            ):
                if path.is_symlink():
                    raise OciTerraformBootstrapError(f"OCI {label} must not be a symlink")
                if path.exists() and not path.is_dir():
                    raise OciTerraformBootstrapError(f"OCI {label} must be a directory")
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            if self.plan_path.is_symlink() or (
                self.plan_path.exists() and not self.plan_path.is_file()
            ):
                raise OciTerraformBootstrapError("Saved OCI plan must be a regular file")
            if self.plan_path.exists() and not preserve_plan:
                self.plan_path.unlink()
            for existing in self.workspace.iterdir():
                is_source = existing.suffix == ".tf" or existing.name.endswith(".tf.json")
                if not is_source and existing.name != ".terraform.lock.hcl":
                    continue
                if existing.is_symlink() or not existing.is_file():
                    raise OciTerraformBootstrapError(
                        "OCI Terraform workspace files must be regular files"
                    )
                existing.unlink()
            for source in self.infra_dir.iterdir():
                if source.is_file() and (
                    source.suffix == ".tf" or source.name == ".terraform.lock.hcl"
                ):
                    shutil.copy2(source, self.workspace / source.name)
        except OSError as error:
            raise OciTerraformBootstrapError(
                "Could not prepare the OCI operator artifact directory"
            ) from error

    def require_plan(self) -> None:
        if not self.plan_path.is_file() or self.plan_path.is_symlink():
            raise OciTerraformBootstrapError(f"Saved OCI plan is missing: {self.plan_path}")

    def secure_plan(self) -> None:
        self.require_plan()
        try:
            self.plan_path.chmod(0o600)
        except OSError as error:
            raise OciTerraformBootstrapError("Could not secure the saved OCI plan") from error

    def run(self, *args: str) -> None:
        environment = os.environ.copy()
        environment["TF_DATA_DIR"] = str(self.tf_data_dir)
        try:
            subprocess.run(
                args,
                cwd=self.workspace,
                check=True,
                env=environment,
                umask=0o077,
            )
        except FileNotFoundError as error:
            raise OciTerraformBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            raise OciTerraformBootstrapError(
                f"Terraform {args[1]} failed with exit code {error.returncode}"
            ) from error

    def verify_no_drift(self, *args: str) -> None:
        environment = os.environ.copy()
        environment["TF_DATA_DIR"] = str(self.tf_data_dir)
        try:
            completed = subprocess.run(
                args,
                cwd=self.workspace,
                check=False,
                env=environment,
                umask=0o077,
            )
        except FileNotFoundError as error:
            raise OciTerraformBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        if completed.returncode == 0:
            return
        if completed.returncode == 2:
            raise OciTerraformBootstrapError("OCI deployment verification found Terraform drift")
        raise OciTerraformBootstrapError(
            f"Terraform plan failed with exit code {completed.returncode}"
        )


def _repository_root(infra_dir: Path) -> Path:
    for candidate in (infra_dir, *infra_dir.parents):
        candidate_infra = candidate / "infra"
        if candidate_infra.is_dir() and (
            infra_dir == candidate_infra or candidate_infra in infra_dir.parents
        ):
            return candidate.resolve()
    raise OciTerraformBootstrapError("Could not locate the project root for OCI bootstrap")


def _require(pattern: re.Pattern[str], value: str, label: str) -> None:
    if pattern.fullmatch(value) is None:
        raise OciTerraformBootstrapError(f"Invalid {label}: {value!r}")


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError as error:
        raise OciTerraformBootstrapError("Could not write OCI backend metadata") from error
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "OciAdministrativeBootstrap",
    "OciTerraformBootstrap",
    "OciTerraformBootstrapError",
    "build_oci_execution_projections",
]
