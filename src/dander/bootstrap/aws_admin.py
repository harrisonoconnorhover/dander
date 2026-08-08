"""Plan-first AWS stage-zero bootstrap with post-apply remote-state migration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_AWS_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_DEPLOYMENT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_DYNAMODB_TABLE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_ECR_REPOSITORY = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
_PRINCIPAL_ARN = re.compile(
    r"^arn:(?:aws|aws-us-gov):iam::(?P<account>[0-9]{12}):"
    r"(?:root|user/[A-Za-z0-9+=,.@_/-]+|role/[A-Za-z0-9+=,.@_/-]+)$"
)
_S3_BUCKET = re.compile(r"^(?![0-9]+(?:\.[0-9]+){3}$)[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_STATE_KEY = re.compile(r"^(?!/)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_BACKEND_SCHEMA = "io.dander.aws-bootstrap-backend/v1"


class AwsAdministrativeBootstrapError(RuntimeError):
    """Raised when AWS stage zero cannot be planned or applied safely."""


class AwsAdministrativeBootstrap:
    """Create AWS state, registry, and deployment-role prerequisites through a saved plan."""

    def __init__(self, infra_dir: Path, operator_artifact_dir: Path) -> None:
        self._infra_dir = infra_dir.resolve()
        self._repository_dir = self._infra_dir.parent.parent.parent
        self._operator_artifact_dir = operator_artifact_dir.expanduser().resolve()
        if self._is_within(self._operator_artifact_dir, self._repository_dir):
            raise AwsAdministrativeBootstrapError(
                "AWS operator artifact directory must be outside the repository checkout"
            )
        self._workspace = self._operator_artifact_dir / "terraform-workspace"
        self._tf_data_dir = self._operator_artifact_dir / "terraform-data"
        self._plan_path = self._operator_artifact_dir / "dander-aws-admin-bootstrap.tfplan"
        self._backend_path = self._workspace / "backend.tf.json"
        self._backend_record_path = self._operator_artifact_dir / "backend.json"
        self._local_state_path = self._operator_artifact_dir / "terraform.tfstate"

    def execute(
        self,
        *,
        aws_account_id: str,
        region: str,
        state_bucket: str,
        state_key: str,
        lock_table: str,
        ecr_repository_name: str,
        admin_principal_arn: str,
        aws_profile: str = "",
        name: str = "dander",
    ) -> Path:
        """Save a stage-zero plan without applying or creating cloud resources."""
        self._validate(
            aws_account_id=aws_account_id,
            region=region,
            state_bucket=state_bucket,
            state_key=state_key,
            lock_table=lock_table,
            ecr_repository_name=ecr_repository_name,
            admin_principal_arn=admin_principal_arn,
            aws_profile=aws_profile,
            name=name,
        )
        self._prepare_operator_directories()
        backend = self._load_backend_record()
        if backend is None:
            self._write_backend(
                "local",
                {"path": str(self._local_state_path)},
            )
        else:
            self._require_matching_backend(
                backend,
                state_bucket=state_bucket,
                state_key=state_key,
                region=region,
                lock_table=lock_table,
            )
            self._write_s3_backend(
                state_bucket=state_bucket,
                state_key=state_key,
                region=region,
                lock_table=lock_table,
            )
        self._run("terraform", "init", "-reconfigure", "-input=false", aws_profile=aws_profile)
        self._run(
            "terraform",
            "plan",
            "-input=false",
            f"-var=aws_account_id={aws_account_id}",
            f"-var=region={region}",
            f"-var=name={name}",
            f"-var=state_bucket={state_bucket}",
            f"-var=lock_table={lock_table}",
            f"-var=ecr_repository_name={ecr_repository_name}",
            f"-var=admin_principal_arn={admin_principal_arn}",
            f"-out={self._plan_path}",
            aws_profile=aws_profile,
        )
        self._secure_plan()
        return self._plan_path

    def apply_saved_plan(
        self,
        *,
        aws_account_id: str,
        region: str,
        state_bucket: str,
        state_key: str,
        lock_table: str,
        ecr_repository_name: str,
        admin_principal_arn: str,
        aws_profile: str = "",
        name: str = "dander",
    ) -> Path:
        """Apply only the saved plan, then migrate initial local state into S3."""
        self._validate(
            aws_account_id=aws_account_id,
            region=region,
            state_bucket=state_bucket,
            state_key=state_key,
            lock_table=lock_table,
            ecr_repository_name=ecr_repository_name,
            admin_principal_arn=admin_principal_arn,
            aws_profile=aws_profile,
            name=name,
        )
        self._prepare_operator_directories(preserve_plan=True)
        if not self._plan_path.is_file() or self._plan_path.is_symlink():
            raise AwsAdministrativeBootstrapError(
                f"No reviewed AWS stage-zero plan exists at {self._plan_path}"
            )
        backend = self._load_backend_record()
        if backend is None:
            self._write_backend("local", {"path": str(self._local_state_path)})
        else:
            self._require_matching_backend(
                backend,
                state_bucket=state_bucket,
                state_key=state_key,
                region=region,
                lock_table=lock_table,
            )
            self._write_s3_backend(
                state_bucket=state_bucket,
                state_key=state_key,
                region=region,
                lock_table=lock_table,
            )
        self._run("terraform", "init", "-reconfigure", "-input=false", aws_profile=aws_profile)
        self._run(
            "terraform", "apply", "-input=false", str(self._plan_path), aws_profile=aws_profile
        )
        if backend is None:
            self._migrate_state(
                state_bucket=state_bucket,
                state_key=state_key,
                region=region,
                lock_table=lock_table,
                aws_profile=aws_profile,
            )
        return self._plan_path

    @staticmethod
    def deployment_role_arn(*, aws_account_id: str, region: str, name: str) -> str:
        """Return the exact stage-zero deployment-role ARN."""
        partition = "aws-us-gov" if region.startswith("us-gov-") else "aws"
        return f"arn:{partition}:iam::{aws_account_id}:role/{name}-bootstrap"

    def _migrate_state(
        self,
        *,
        state_bucket: str,
        state_key: str,
        region: str,
        lock_table: str,
        aws_profile: str,
    ) -> None:
        self._write_s3_backend(
            state_bucket=state_bucket,
            state_key=state_key,
            region=region,
            lock_table=lock_table,
        )
        try:
            self._run(
                "terraform",
                "init",
                "-migrate-state",
                "-force-copy",
                "-input=false",
                aws_profile=aws_profile,
            )
        except AwsAdministrativeBootstrapError:
            self._write_backend("local", {"path": str(self._local_state_path)})
            raise
        self._write_json(
            self._backend_record_path,
            {
                "schema": _BACKEND_SCHEMA,
                "bucket": state_bucket,
                "key": state_key,
                "region": region,
                "dynamodb_table": lock_table,
            },
        )

    def _prepare_operator_directories(self, *, preserve_plan: bool = False) -> None:
        try:
            for path, label in (
                (self._operator_artifact_dir, "operator artifact directory"),
                (self._workspace, "Terraform workspace"),
                (self._tf_data_dir, "terraform-data"),
            ):
                if path.is_symlink():
                    raise AwsAdministrativeBootstrapError(f"{label} must not be a symlink")
                if path.exists() and not path.is_dir():
                    raise AwsAdministrativeBootstrapError(f"{label} must be a directory")
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            if self._plan_path.is_symlink():
                raise AwsAdministrativeBootstrapError("Saved AWS plan must not be a symlink")
            if self._plan_path.exists() and not self._plan_path.is_file():
                raise AwsAdministrativeBootstrapError("Saved AWS plan must be a regular file")
            if self._plan_path.exists() and not preserve_plan:
                self._plan_path.unlink()
            for path, label in (
                (self._local_state_path, "Local Terraform state"),
                (self._backend_record_path, "AWS backend record"),
                (self._backend_path, "Terraform backend configuration"),
            ):
                if path.is_symlink():
                    raise AwsAdministrativeBootstrapError(f"{label} must not be a symlink")
                if path.exists() and not path.is_file():
                    raise AwsAdministrativeBootstrapError(f"{label} must be a regular file")
            for existing in self._workspace.iterdir():
                is_terraform_source = existing.suffix == ".tf" or (
                    existing.name.endswith(".tf.json") and existing != self._backend_path
                )
                if not is_terraform_source and existing.name != ".terraform.lock.hcl":
                    continue
                if existing.is_symlink() or not existing.is_file():
                    raise AwsAdministrativeBootstrapError(
                        "Terraform workspace files must be regular files, not symlinks"
                    )
                existing.unlink()
            for source in self._infra_dir.iterdir():
                if source.is_file() and (
                    source.suffix == ".tf" or source.name == ".terraform.lock.hcl"
                ):
                    destination = self._workspace / source.name
                    if destination.is_symlink():
                        raise AwsAdministrativeBootstrapError(
                            "Terraform workspace files must not be symlinks"
                        )
                    shutil.copy2(source, destination)
        except OSError as error:
            raise AwsAdministrativeBootstrapError(
                "Could not prepare the AWS operator artifact directory"
            ) from error

    def _secure_plan(self) -> None:
        try:
            if not self._plan_path.is_file() or self._plan_path.is_symlink():
                raise AwsAdministrativeBootstrapError(
                    f"Terraform did not create a regular saved plan at {self._plan_path}"
                )
            self._plan_path.chmod(0o600)
        except OSError as error:
            raise AwsAdministrativeBootstrapError(
                "Could not secure the saved AWS Terraform plan"
            ) from error

    def _load_backend_record(self) -> dict[str, object] | None:
        if not self._backend_record_path.exists():
            return None
        if not self._backend_record_path.is_file() or self._backend_record_path.is_symlink():
            raise AwsAdministrativeBootstrapError("AWS backend record must be a regular file")
        try:
            document = json.loads(self._backend_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AwsAdministrativeBootstrapError("AWS backend record is unreadable") from error
        if not isinstance(document, dict) or document.get("schema") != _BACKEND_SCHEMA:
            raise AwsAdministrativeBootstrapError("AWS backend record has an unsupported schema")
        return document

    @staticmethod
    def _require_matching_backend(
        backend: dict[str, object],
        *,
        state_bucket: str,
        state_key: str,
        region: str,
        lock_table: str,
    ) -> None:
        expected = {
            "bucket": state_bucket,
            "key": state_key,
            "region": region,
            "dynamodb_table": lock_table,
        }
        if any(backend.get(key) != value for key, value in expected.items()):
            raise AwsAdministrativeBootstrapError(
                "AWS backend inputs do not match the previously migrated stage-zero state"
            )

    def _write_s3_backend(
        self,
        *,
        state_bucket: str,
        state_key: str,
        region: str,
        lock_table: str,
    ) -> None:
        self._write_backend(
            "s3",
            {
                "bucket": state_bucket,
                "key": state_key,
                "region": region,
                "encrypt": True,
                "dynamodb_table": lock_table,
            },
        )

    def _write_backend(self, backend_type: str, config: dict[str, object]) -> None:
        self._write_json(
            self._backend_path,
            {"terraform": {"backend": {backend_type: config}}},
        )

    @staticmethod
    def _write_json(path: Path, document: dict[str, object]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, path)
            path.chmod(0o600)
        except OSError as error:
            raise AwsAdministrativeBootstrapError("Could not write AWS backend metadata") from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _is_within(candidate: Path, parent: Path) -> bool:
        return candidate == parent or parent in candidate.parents

    @staticmethod
    def _validate(
        *,
        aws_account_id: str,
        region: str,
        state_bucket: str,
        state_key: str,
        lock_table: str,
        ecr_repository_name: str,
        admin_principal_arn: str,
        aws_profile: str,
        name: str,
    ) -> None:
        for label, value, pattern in (
            ("AWS account id", aws_account_id, _AWS_ACCOUNT_ID),
            ("AWS region", region, _AWS_REGION),
            ("S3 state bucket", state_bucket, _S3_BUCKET),
            ("state key", state_key, _STATE_KEY),
            ("DynamoDB lock table", lock_table, _DYNAMODB_TABLE),
            ("ECR repository", ecr_repository_name, _ECR_REPOSITORY),
            ("deployment name", name, _DEPLOYMENT_NAME),
        ):
            if not pattern.fullmatch(value):
                raise AwsAdministrativeBootstrapError(f"Invalid {label}: {value!r}")
        principal = _PRINCIPAL_ARN.fullmatch(admin_principal_arn)
        if principal is None or principal.group("account") != aws_account_id:
            raise AwsAdministrativeBootstrapError(
                "AWS admin principal must be an IAM root, user, or role ARN in the target account"
            )
        if aws_profile and not _AWS_PROFILE.fullmatch(aws_profile):
            raise AwsAdministrativeBootstrapError(f"Invalid AWS profile: {aws_profile!r}")

    def _run(self, *args: str, aws_profile: str) -> None:
        environment = os.environ.copy()
        environment["TF_DATA_DIR"] = str(self._tf_data_dir)
        if aws_profile:
            environment["AWS_PROFILE"] = aws_profile
        try:
            subprocess.run(
                args,
                cwd=self._workspace,
                check=True,
                env=environment,
                umask=0o077,
            )
        except FileNotFoundError as error:
            raise AwsAdministrativeBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            raise AwsAdministrativeBootstrapError(
                f"Terraform {args[1]} failed with exit code {error.returncode}"
            ) from error


__all__ = ["AwsAdministrativeBootstrap", "AwsAdministrativeBootstrapError"]
