"""Deterministic AWS Fargate/CloudFront projection and read-only D7 verifier."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dander.control.auth import HostedOIDCDeploymentInput, project_hosted_oidc
from dander.control.orchestration import ExecutionPlan, TriggerKind, TriggerSpec
from dander.control.orchestration_serialization import (
    OrchestrationSerializationError,
    deserialize_execution_plan,
    deserialize_trigger_spec,
    render_schedule_wakeup_template,
)
from dander.deployment.service import (
    ControlServiceIngress,
    ControlServiceObservability,
    ControlServiceProbes,
    ControlServiceResources,
    ControlServiceScaling,
    IngressVisibility,
    ResolvedControlServiceRequest,
    S3GraphStoreBinding,
)
from dander.project.portable_config import DanderPlatforms
from dander.providers.fargate.operations import fargate_resource_name

AWS_CONTROL_PLANE_SCHEMA: Final = "io.dander.aws-control-plane/v1"
AWS_CONTROL_PORT: Final = 8770
AWS_DRUFF_PORT: Final = 8080
AWS_CONFIG_ROOT: Final = "/etc/dander"
AWS_ORCHESTRATION_ROOT: Final = f"{AWS_CONFIG_ROOT}/orchestration"
AWS_PLATFORMS_CONFIG: Final = f"{AWS_CONFIG_ROOT}/dander.platforms.yaml"
AWS_D7_STATE_PREFIX: Final = "dander/d7/control-plane/"

_ACCOUNT = re.compile(r"^[0-9]{12}$")
_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_VPC = re.compile(r"^vpc-[0-9a-f]{8,17}$")
_SUBNET = re.compile(r"^subnet-[0-9a-f]{8,17}$")
_DISTRIBUTION = re.compile(r"^[A-Z0-9]{8,32}$")
_CLOUDFRONT_HOST = re.compile(r"^[a-z0-9]{8,32}\.cloudfront\.net$")
_ROLE_ARN = re.compile(
    r"^arn:(?:aws|aws-us-gov):iam::(?P<account>[0-9]{12}):role/[A-Za-z0-9+=,.@_/-]+$"
)
_PRIVATE_IMAGE = re.compile(
    r"^(?P<account>[0-9]{12})\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com/"
    r"(?P<repository>[a-z0-9]+(?:[._/-][a-z0-9]+)*)@sha256:[0-9a-f]{64}$"
)
_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SCHEDULE_EXPRESSION = re.compile(r"^(?:cron|rate|at)\(.+\)$")
_MAX_ORCHESTRATION_CONFIG_BYTES: Final = 512 * 1024
_MAX_PLATFORMS_CONFIG_BYTES: Final = 32 * 1024
_FILES: Final = frozenset(
    {
        "Caddyfile",
        "active.tfvars.json",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "foundation.tfvars.json",
        "public-client.json",
        "rollback.tfvars.json",
    }
)

_CSP: Final = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
    "form-action 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self' https:; worker-src 'self' blob:; "
    "frame-src 'none'; media-src 'none'; manifest-src 'self'"
)

_CADDYFILE: Final = """{
  admin off
  auto_https off
  persist_config off
}

:8080 {
  encode zstd gzip

  route {
    header {
      Cache-Control "no-store"
      Content-Security-Policy "__DANDER_CSP__"
      Cross-Origin-Opener-Policy "same-origin"
      Cross-Origin-Resource-Policy "same-origin"
      Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
      Referrer-Policy "no-referrer"
      Strict-Transport-Security "max-age=31536000; includeSubDomains"
      X-Content-Type-Options "nosniff"
      X-Frame-Options "DENY"
    }

    handle /healthz {
      header Content-Type application/json
      respond `{"status":"ok"}` 200
    }

    handle /readyz {
      header Content-Type application/json
      respond `{"status":"ready"}` 200
    }

    handle /bootstrap.json {
      root * /etc/dander/bootstrap
      file_server
    }

    handle {
      root * /app
      @immutable path /_next/static/*
      header @immutable Cache-Control "public, max-age=31536000, immutable"
      try_files {path}.html {path}
      file_server
    }
  }
}
""".replace("__DANDER_CSP__", _CSP)


class AWSControlPlaneError(ValueError):
    """The AWS profile, projection, or live deployment is invalid."""


def _parse_platforms_config(value: str) -> DanderPlatforms:
    try:
        document = yaml.safe_load(value)
        return DanderPlatforms.model_validate(document)
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError("AWS Control platform configuration must be valid YAML.") from error


class AWSControlPlaneFoundationInput(BaseModel):
    """Closed non-secret coordinates needed before CloudFront assigns its domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    aws_account_id: str
    region: str
    name: str = "dander"
    deployment_role_arn: str
    state_bucket: str
    state_prefix: str
    lock_table: str = Field(min_length=3, max_length=255)
    ecr_repository_url: str = Field(min_length=1, max_length=512)
    graph_bucket: str
    vpc_id: str
    subnet_ids: tuple[str, ...]

    @field_validator("aws_account_id")
    @classmethod
    def validate_account(cls, value: str) -> str:
        if _ACCOUNT.fullmatch(value) is None:
            raise ValueError("AWS account id must contain twelve digits.")
        return value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        if _REGION.fullmatch(value) is None:
            raise ValueError("AWS region is invalid.")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError("AWS profile name is invalid.")
        return value

    @field_validator("state_bucket", "graph_bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        if (
            _BUCKET.fullmatch(value) is None
            or ".." in value
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", value) is not None
        ):
            raise ValueError("AWS bucket name is invalid.")
        return value

    @field_validator("state_prefix")
    @classmethod
    def validate_state_prefix(cls, value: str) -> str:
        if (
            not value.startswith(AWS_D7_STATE_PREFIX)
            or value == AWS_D7_STATE_PREFIX
            or value.endswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("AWS D7 state must use one child of the fixed D7 prefix.")
        return value

    @field_validator("vpc_id")
    @classmethod
    def validate_vpc(cls, value: str) -> str:
        if _VPC.fullmatch(value) is None:
            raise ValueError("AWS VPC id is invalid.")
        return value

    @field_validator("subnet_ids")
    @classmethod
    def validate_subnets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if len(normalized) < 2 or any(_SUBNET.fullmatch(item) is None for item in normalized):
            raise ValueError("AWS profile requires at least two distinct subnet ids.")
        return normalized

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        role = _ROLE_ARN.fullmatch(self.deployment_role_arn)
        if role is None or role.group("account") != self.aws_account_id:
            raise ValueError("AWS deployment role must belong to the selected account.")
        expected_repository = f"{self.aws_account_id}.dkr.ecr.{self.region}.amazonaws.com/"
        if not self.ecr_repository_url.startswith(expected_repository):
            raise ValueError("AWS ECR repository must belong to the selected account and region.")
        if self.state_bucket == self.graph_bucket:
            raise ValueError("AWS state and graph buckets must differ.")
        prefix = f"{self.name}-d7-"
        if not self.graph_bucket.startswith(prefix):
            raise ValueError("The disposable AWS bucket must use the reviewed D7 name prefix.")
        return self


class AWSControlPlaneInput(AWSControlPlaneFoundationInput):
    """One closed immutable non-secret input for the complete AWS D7 profile."""

    cloudfront_distribution_id: str
    cloudfront_domain: str
    dander_image: str = Field(min_length=1, max_length=2048)
    dander_rollback_image: str = Field(min_length=1, max_length=2048)
    druff_image: str = Field(min_length=1, max_length=2048)
    druff_rollback_image: str = Field(min_length=1, max_length=2048)
    oidc: HostedOIDCDeploymentInput
    platforms_config_yaml: str = ""
    execution_plan_json: tuple[str, ...] = ()
    trigger_spec_json: tuple[str, ...] = ()
    run_environment: str = "production"
    fargate_deployment_name: str = "dander"

    @field_validator("cloudfront_distribution_id")
    @classmethod
    def validate_distribution(cls, value: str) -> str:
        if _DISTRIBUTION.fullmatch(value) is None:
            raise ValueError("CloudFront distribution id is invalid.")
        return value

    @field_validator("cloudfront_domain")
    @classmethod
    def validate_cloudfront_domain(cls, value: str) -> str:
        if _CLOUDFRONT_HOST.fullmatch(value) is None:
            raise ValueError("CloudFront domain is invalid.")
        return value

    @field_validator(
        "dander_image",
        "dander_rollback_image",
        "druff_image",
        "druff_rollback_image",
    )
    @classmethod
    def validate_application_image(cls, value: str) -> str:
        if _PRIVATE_IMAGE.fullmatch(value) is None:
            raise ValueError("AWS application images must use immutable private-ECR digests.")
        return value

    @field_validator("run_environment")
    @classmethod
    def validate_run_environment(cls, value: str) -> str:
        if _PORTABLE_ID.fullmatch(value) is None:
            raise ValueError("AWS Control run environment is invalid.")
        return value

    @field_validator("fargate_deployment_name")
    @classmethod
    def validate_fargate_deployment_name(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError("AWS Fargate deployment name is invalid.")
        return value

    @field_validator("platforms_config_yaml")
    @classmethod
    def validate_platforms_config_yaml(cls, value: str) -> str:
        if not value:
            return value
        if len(value.encode("utf-8")) > _MAX_PLATFORMS_CONFIG_BYTES:
            raise ValueError("AWS Control platform configuration is oversized.")
        _parse_platforms_config(value)
        return value

    @field_validator("execution_plan_json")
    @classmethod
    def validate_execution_plan_json(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        try:
            plans = tuple(deserialize_execution_plan(item.encode("utf-8")) for item in value)
        except (AttributeError, OrchestrationSerializationError) as error:
            raise ValueError("AWS Control execution plans must be canonical JSON.") from error
        if len(plans) > 100 or len({plan.revision for plan in plans}) != len(plans):
            raise ValueError("AWS Control execution plans must contain at most 100 revisions.")
        if any(plan.backend_id != "fargate" for plan in plans):
            raise ValueError("AWS Control execution plans must select the Fargate backend.")
        if sum(len(item.encode("utf-8")) for item in value) > _MAX_ORCHESTRATION_CONFIG_BYTES:
            raise ValueError("AWS Control execution-plan configuration is oversized.")
        return value

    @field_validator("trigger_spec_json")
    @classmethod
    def validate_trigger_spec_json(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        try:
            specs = tuple(deserialize_trigger_spec(item.encode("utf-8")) for item in value)
        except (AttributeError, OrchestrationSerializationError) as error:
            raise ValueError("AWS Control trigger specs must be canonical JSON.") from error
        if len(specs) > 100 or len({spec.trigger_id for spec in specs}) != len(specs):
            raise ValueError("AWS Control trigger specs must contain at most 100 unique ids.")
        if sum(len(item.encode("utf-8")) for item in value) > _MAX_ORCHESTRATION_CONFIG_BYTES:
            raise ValueError("AWS Control trigger configuration is oversized.")
        for spec in specs:
            if (
                spec.kind is not TriggerKind.SCHEDULE
                or spec.schedule is None
                or spec.schedule != spec.schedule.strip()
                or _SCHEDULE_EXPRESSION.fullmatch(spec.schedule) is None
                or spec.time_zone is None
                or len(spec.time_zone) > 256
            ):
                raise ValueError(
                    "AWS Control trigger specs must use EventBridge Scheduler expressions."
                )
        return value

    @model_validator(mode="after")
    def validate_complete_profile(self) -> Self:
        images = (
            self.dander_image,
            self.dander_rollback_image,
            self.druff_image,
            self.druff_rollback_image,
        )
        for image in images:
            match = _PRIVATE_IMAGE.fullmatch(image)
            if (
                match is None
                or match.group("account") != self.aws_account_id
                or match.group("region") != self.region
                or image.split("@", 1)[0] != self.ecr_repository_url
            ):
                raise ValueError("AWS application images must use the selected ECR repository.")
        _validate_rollback_pair(self.dander_image, self.dander_rollback_image, "Dander")
        _validate_rollback_pair(self.druff_image, self.druff_rollback_image, "Druff")
        origin = self.browser_origin
        expected = {
            "api_url": origin,
            "redirect_uri": f"{origin}/auth/callback",
            "logout_uri": f"{origin}/signed-out",
            "allowed_origins": (origin,),
        }
        observed = {
            "api_url": self.oidc.api_url,
            "redirect_uri": self.oidc.redirect_uri,
            "logout_uri": self.oidc.logout_uri,
            "allowed_origins": self.oidc.allowed_origins,
        }
        if observed != expected:
            raise ValueError("AWS hosted OIDC routes must use the exact CloudFront origin.")
        plans = self.execution_plans
        triggers = self.trigger_specs
        if plans and not self.platforms_config_yaml:
            raise ValueError("AWS Control execution plans require platform configuration.")
        if plans:
            platforms = _parse_platforms_config(self.platforms_config_yaml)
            for plan in plans:
                deployment = platforms.deployments.get(plan.profile_id)
                if deployment is None:
                    raise ValueError(
                        "AWS Control execution plans require matching platform deployments."
                    )
                launcher = deployment.launcher
                if (
                    launcher.provider != "fargate"
                    or launcher.aws_account_id != self.aws_account_id
                    or launcher.region != self.region
                    or plan.execution_template.pipeline_id not in deployment.pipelines
                ):
                    raise ValueError(
                        "AWS Control execution plans require matching Fargate platform bindings."
                    )
        if triggers and not plans:
            raise ValueError("AWS Control scheduled triggers require execution plans.")
        if plans and not any(plan.environment == self.run_environment for plan in plans):
            raise ValueError("AWS Control run environment has no configured execution plan.")
        by_revision = {plan.revision: plan for plan in plans}
        for spec in triggers:
            scheduled_plan = by_revision.get(spec.plan_revision)
            if scheduled_plan is None or scheduled_plan.plan_id != spec.plan_id:
                raise ValueError(
                    "AWS Control scheduled triggers must select an exact configured plan."
                )
        return self

    @property
    def execution_plans(self) -> tuple[ExecutionPlan, ...]:
        """Return validated plans in deterministic revision order."""
        return tuple(
            sorted(
                (
                    deserialize_execution_plan(item.encode("utf-8"))
                    for item in self.execution_plan_json
                ),
                key=lambda plan: plan.revision,
            )
        )

    @property
    def trigger_specs(self) -> tuple[TriggerSpec, ...]:
        """Return validated schedule triggers in deterministic id order."""
        return tuple(
            sorted(
                (deserialize_trigger_spec(item.encode("utf-8")) for item in self.trigger_spec_json),
                key=lambda spec: spec.trigger_id,
            )
        )

    @property
    def browser_origin(self) -> str:
        return f"https://{self.cloudfront_domain}"

    @property
    def control_task_role_arn(self) -> str:
        partition = "aws-us-gov" if self.region.startswith("us-gov-") else "aws"
        return f"arn:{partition}:iam::{self.aws_account_id}:role/{self.name}-d7-control-task"


def project_aws_control_service(source: AWSControlPlaneInput) -> ResolvedControlServiceRequest:
    """Project the D6 service contract for Fargate and S3."""
    return ResolvedControlServiceRequest(
        service_id="dander_control",
        profile_id="aws_fargate",
        image=source.dander_image,
        port=AWS_CONTROL_PORT,
        probes=ControlServiceProbes(),
        resources=ControlServiceResources(cpu_millis=1000, memory_mib=1024),
        scaling=ControlServiceScaling(
            minimum_instances=1,
            maximum_instances=1,
            shutdown_grace_seconds=30,
        ),
        environment=(),
        secret_bindings=(),
        workload_identity=source.control_task_role_arn,
        ingress=ControlServiceIngress(visibility=IngressVisibility.PUBLIC),
        oidc=source.oidc,
        oidc_config_path=f"{AWS_CONFIG_ROOT}/oidc/control-oidc.json",
        graph_store_config_path=f"{AWS_CONFIG_ROOT}/graph-store/control-graph-store.json",
        graph_store=S3GraphStoreBinding(
            bucket=source.graph_bucket,
            expected_bucket_owner=source.aws_account_id,
        ),
        observability=ControlServiceObservability(
            log_destination="aws-cloudwatch-logs",
            alert_target=None,
            retention_days=1,
        ),
        rollback_digest=_digest(source.dander_rollback_image),
    )


def _control_fargate_bindings(source: AWSControlPlaneInput) -> dict[str, dict[str, str]]:
    partition = "aws-us-gov" if source.region.startswith("us-gov-") else "aws"
    bindings: dict[str, dict[str, str]] = {}
    for plan in source.execution_plans:
        pipeline_id = plan.execution_template.pipeline_id
        resource_name = fargate_resource_name(
            name=source.fargate_deployment_name,
            pipeline_id=pipeline_id,
        )
        bindings[plan.revision] = {
            "execution_arn_prefix": (
                f"arn:{partition}:states:{source.region}:{source.aws_account_id}:"
                f"execution:{resource_name}:"
            ),
            "log_group_arn": (
                f"arn:{partition}:logs:{source.region}:{source.aws_account_id}:"
                f"log-group:/dander/{source.fargate_deployment_name}/{pipeline_id}:*"
            ),
            "state_machine_arn": (
                f"arn:{partition}:states:{source.region}:{source.aws_account_id}:"
                f"stateMachine:{resource_name}"
            ),
        }
    return bindings


def render_aws_control_foundation(source: AWSControlPlaneFoundationInput) -> dict[str, str]:
    """Render only coordinates safe to apply before CloudFront assigns a domain."""
    foundation = {
        **_foundation_values(source),
        "foundation_only": True,
        "dander_image": None,
        "druff_image": None,
        "control_args": [],
        "control_oidc_json": "",
        "graph_store_json": "",
        "platforms_config_yaml": "",
        "bootstrap_json": "",
        "druff_caddyfile": "",
        "execution_plan_json": {},
        "trigger_spec_json": {},
        "control_schedules": {},
        "control_fargate_bindings": {},
    }
    manifest = {
        "schema": AWS_CONTROL_PLANE_SCHEMA,
        "stage": "foundation",
        "aws_account_id": source.aws_account_id,
        "region": source.region,
        "name": source.name,
        "state_bucket": source.state_bucket,
        "state_prefix": source.state_prefix,
        "deployment_role_arn": source.deployment_role_arn,
        "ecr_repository_url": source.ecr_repository_url,
        "graph_bucket": source.graph_bucket,
        "vpc_id": source.vpc_id,
        "subnet_ids": list(source.subnet_ids),
    }
    return {
        "deployment.json": _json(manifest),
        "foundation.tfvars.json": _json(foundation),
    }


def render_aws_control_plane(source: AWSControlPlaneInput) -> dict[str, str]:
    """Return the exact provider files without contacting AWS or Terraform."""
    oidc = project_hosted_oidc(source.oidc)
    service = project_aws_control_service(source)
    oidc_json = _json(source.oidc.model_dump(mode="json"))
    graph_json = _json(service.graph_store.as_dict())
    bootstrap_json = _json(oidc.bootstrap.model_dump(mode="json"))
    public_client_json = _json(oidc.public_client.model_dump(mode="json"))
    plan_json = {
        deserialize_execution_plan(raw.encode("utf-8")).revision: raw
        for raw in source.execution_plan_json
    }
    trigger_json = {
        deserialize_trigger_spec(raw.encode("utf-8")).trigger_id: raw
        for raw in source.trigger_spec_json
    }
    schedules = {
        spec.trigger_id: {
            "expression": spec.schedule,
            "time_zone": spec.time_zone,
            "plan_revision": spec.plan_revision,
            "enabled": spec.enabled,
            "message": render_schedule_wakeup_template(spec),
        }
        for spec in source.trigger_specs
    }
    control_args = list(service.command)
    if source.execution_plans:
        control_args.extend(("--platforms-config", AWS_PLATFORMS_CONFIG))
        for project in sorted({plan.project for plan in source.execution_plans}):
            control_args.extend(("--project", project))
        control_args.extend(("--aws-deployment-name", source.fargate_deployment_name))
        for plan in source.execution_plans:
            control_args.extend(
                (
                    "--execution-plan",
                    f"{AWS_ORCHESTRATION_ROOT}/plans/{plan.revision}.json",
                )
            )
        control_args.extend(
            (
                "--run-store-bucket",
                source.graph_bucket,
                "--run-store-prefix",
                "dander-control/v1",
                "--run-environment",
                source.run_environment,
            )
        )
        for spec in source.trigger_specs:
            control_args.extend(
                (
                    "--trigger-spec",
                    f"{AWS_ORCHESTRATION_ROOT}/triggers/{spec.trigger_id}.json",
                )
            )
    common: dict[str, object] = {
        **_foundation_values(source),
        "foundation_only": False,
        "cloudfront_distribution_id": source.cloudfront_distribution_id,
        "cloudfront_domain": source.cloudfront_domain,
        "control_args": control_args,
        "control_oidc_json": oidc_json,
        "graph_store_json": graph_json,
        "platforms_config_yaml": source.platforms_config_yaml,
        "bootstrap_json": bootstrap_json,
        "druff_caddyfile": _CADDYFILE,
        "execution_plan_json": plan_json,
        "trigger_spec_json": trigger_json,
        "control_schedules": schedules,
        "control_fargate_bindings": _control_fargate_bindings(source),
    }
    active = {
        **common,
        "dander_image": source.dander_image,
        "druff_image": source.druff_image,
    }
    rollback = {
        **common,
        "dander_image": source.dander_rollback_image,
        "druff_image": source.druff_rollback_image,
    }
    foundation = render_aws_control_foundation(source)["foundation.tfvars.json"]
    manifest = {
        "schema": AWS_CONTROL_PLANE_SCHEMA,
        "stage": "complete",
        "aws_account_id": source.aws_account_id,
        "region": source.region,
        "name": source.name,
        "state_bucket": source.state_bucket,
        "state_prefix": source.state_prefix,
        "deployment_role_arn": source.deployment_role_arn,
        "browser_origin": source.browser_origin,
        "cloudfront_distribution_id": source.cloudfront_distribution_id,
        "graph_bucket": source.graph_bucket,
        "service": service.as_dict(),
        "active_images": {"dander": source.dander_image, "druff": source.druff_image},
        "rollback_images": {
            "dander": source.dander_rollback_image,
            "druff": source.druff_rollback_image,
        },
        "config_sha256": {
            "control_oidc": _sha256(oidc_json),
            "graph_store": _sha256(graph_json),
            "bootstrap": _sha256(bootstrap_json),
            "druff_caddy": _sha256(_CADDYFILE),
            "execution_plans": _sha256(_json(plan_json)),
            "trigger_specs": _sha256(_json(trigger_json)),
        },
        "orchestration": {
            "run_environment": source.run_environment,
            "fargate_deployment_name": source.fargate_deployment_name,
            "execution_plan_revisions": sorted(plan_json),
            "scheduled_trigger_ids": sorted(schedules),
            "run_store_bucket": source.graph_bucket if plan_json else None,
            "run_store_prefix": "dander-control/v1" if plan_json else None,
        },
        "cloudfront": {
            "api_cache_ttl_seconds": 0,
            "api_query_strings": "all",
            "api_cookies": "none",
            "api_headers": "allViewer",
            "static_minimum_ttl_seconds": 0,
            "access_logs": False,
        },
    }
    return {
        "Caddyfile": _CADDYFILE,
        "active.tfvars.json": _json(active),
        "bootstrap.json": bootstrap_json,
        "control-graph-store.json": graph_json,
        "control-oidc.json": oidc_json,
        "deployment.json": _json(manifest),
        "foundation.tfvars.json": foundation,
        "public-client.json": public_client_json,
        "rollback.tfvars.json": _json(rollback),
    }


def write_aws_control_plane(
    source: AWSControlPlaneFoundationInput | AWSControlPlaneInput,
    *,
    output_directory: Path,
) -> tuple[Path, ...]:
    """Atomically write only the closed non-secret AWS projection."""
    destination = output_directory.expanduser().resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    rendered = (
        render_aws_control_plane(source)
        if isinstance(source, AWSControlPlaneInput)
        else render_aws_control_foundation(source)
    )
    written: list[Path] = []
    for name, value in rendered.items():
        if name not in _FILES:
            raise AWSControlPlaneError(f"unsupported AWS projection file: {name}")
        target = destination / name
        with NamedTemporaryFile("w", encoding="utf-8", dir=destination, delete=False) as temporary:
            temporary.write(value)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o444)
            temporary_path = Path(temporary.name)
        temporary_path.replace(target)
        target.chmod(0o444)
        written.append(target)
    return tuple(sorted(written))


def preflight_aws_control_plane(
    source: AWSControlPlaneFoundationInput | AWSControlPlaneInput,
    *,
    output_directory: Path,
    terraform_root: Path,
) -> dict[str, object]:
    """Render and validate the root without backend or provider access."""
    written = write_aws_control_plane(source, output_directory=output_directory)
    environment = {**os.environ, "TF_DATA_DIR": str(output_directory / "terraform-data")}
    commands = (
        ("terraform", f"-chdir={terraform_root}", "fmt", "-check"),
        ("terraform", f"-chdir={terraform_root}", "init", "-backend=false", "-input=false"),
        ("terraform", f"-chdir={terraform_root}", "validate"),
    )
    for command in commands:
        _run(command, environment=environment)
    return {
        "status": "passed",
        "stage": "complete" if isinstance(source, AWSControlPlaneInput) else "foundation",
        "files": [path.name for path in written],
        "terraform_root": str(terraform_root.resolve()),
    }


def verify_live_aws_control_plane(
    source: AWSControlPlaneInput,
    *,
    environment: Literal["active", "rollback"],
) -> dict[str, object]:
    """Read live AWS state and fail closed on provider or public-surface drift."""
    rendered = render_aws_control_plane(source)
    desired_images = (
        {"control": source.dander_image, "druff": source.druff_image}
        if environment == "active"
        else {"control": source.dander_rollback_image, "druff": source.druff_rollback_image}
    )
    caller = _aws_json(source, "sts", "get-caller-identity")
    if not isinstance(caller, Mapping) or caller.get("Account") != source.aws_account_id:
        raise AWSControlPlaneError("authenticated AWS account does not match the projection")
    distribution = _aws_json(
        source,
        "cloudfront",
        "get-distribution",
        "--id",
        source.cloudfront_distribution_id,
    )
    api_cache_id, static_cache_id, origin_policy_id = _verify_distribution(source, distribution)
    _verify_cloudfront_policies(
        source,
        api_cache_id=api_cache_id,
        static_cache_id=static_cache_id,
        origin_policy_id=origin_policy_id,
    )
    _verify_load_balancer(source, distribution)
    cluster = f"{source.name}-d7-control"
    services = _aws_json(
        source,
        "ecs",
        "describe-services",
        "--cluster",
        cluster,
        "--services",
        f"{source.name}-d7-control",
        f"{source.name}-d7-druff",
    )
    target_groups = _verify_services(services)
    for workload, image in desired_images.items():
        task_arns = _aws_json(
            source,
            "ecs",
            "list-tasks",
            "--cluster",
            cluster,
            "--service-name",
            f"{source.name}-d7-{workload}",
            "--desired-status",
            "RUNNING",
        )
        arns = task_arns.get("taskArns") if isinstance(task_arns, Mapping) else None
        if not isinstance(arns, list) or len(arns) != 1 or not isinstance(arns[0], str):
            raise AWSControlPlaneError(f"AWS {workload} service must have one running task")
        tasks = _aws_json(
            source,
            "ecs",
            "describe-tasks",
            "--cluster",
            cluster,
            "--tasks",
            arns[0],
        )
        task_definition_arn = _verify_task(workload, image, tasks)
        task_definition = _aws_json(
            source,
            "ecs",
            "describe-task-definition",
            "--task-definition",
            task_definition_arn,
            "--include",
            "TAGS",
        )
        _verify_task_definition(
            source,
            rendered,
            workload=workload,
            app_image=image,
            init_image=desired_images["control"],
            value=task_definition,
        )
        target_health = _aws_json(
            source,
            "elbv2",
            "describe-target-health",
            "--target-group-arn",
            target_groups[workload],
        )
        _verify_target_health(workload, target_health)
    _verify_bucket(source, source.graph_bucket)
    scheduling = _verify_scheduling(source)
    expected = json.loads(rendered["deployment.json"])["config_sha256"]
    health = {
        "control_health": _http_json(f"{source.browser_origin}/healthz"),
        "control_ready": _http_json(f"{source.browser_origin}/readyz"),
    }
    if health != {
        "control_health": {"status": "ok"},
        "control_ready": {"status": "ready"},
    }:
        raise AWSControlPlaneError("AWS public probes did not return the exact healthy payloads")
    bootstrap, headers = _http_bytes(f"{source.browser_origin}/bootstrap.json")
    if _sha256_bytes(bootstrap) != expected["bootstrap"]:
        raise AWSControlPlaneError("AWS public bootstrap does not match the projection")
    if headers.get("cache-control", "").casefold() != "no-store":
        raise AWSControlPlaneError("AWS bootstrap must remain non-cacheable")
    return {
        "status": "passed",
        "environment": environment,
        "aws_account_id": source.aws_account_id,
        "region": source.region,
        "distribution_id": source.cloudfront_distribution_id,
        "images": desired_images,
        "config_sha256": expected,
        "graph_bucket": source.graph_bucket,
        "scheduling": scheduling,
    }


def _foundation_values(source: AWSControlPlaneFoundationInput) -> dict[str, object]:
    return {
        "aws_account_id": source.aws_account_id,
        "region": source.region,
        "name": source.name,
        "deployment_role_arn": source.deployment_role_arn,
        "ecr_repository_url": source.ecr_repository_url,
        "graph_bucket": source.graph_bucket,
        "vpc_id": source.vpc_id,
        "subnet_ids": list(source.subnet_ids),
    }


def _verify_distribution(source: AWSControlPlaneInput, value: object) -> tuple[str, str, str]:
    if not isinstance(value, Mapping):
        raise AWSControlPlaneError("CloudFront distribution response is malformed")
    distribution = value.get("Distribution")
    if not isinstance(distribution, Mapping):
        raise AWSControlPlaneError("CloudFront distribution is absent")
    config = distribution.get("DistributionConfig")
    if (
        distribution.get("Status") != "Deployed"
        or not isinstance(config, Mapping)
        or config.get("Enabled") is not True
    ):
        raise AWSControlPlaneError("CloudFront distribution is not deployed and enabled")
    if distribution.get("DomainName") != source.cloudfront_domain:
        raise AWSControlPlaneError("CloudFront domain does not match the projection")
    if config.get("Logging", {}).get("Enabled") is not False:
        raise AWSControlPlaneError("CloudFront access logging must remain disabled")
    behaviors = config.get("CacheBehaviors")
    items = behaviors.get("Items") if isinstance(behaviors, Mapping) else None
    if not isinstance(items, list):
        raise AWSControlPlaneError("CloudFront API/probe behaviors are absent")
    mapped = {item.get("PathPattern"): item for item in items if isinstance(item, Mapping)}
    paths = set(mapped)
    if paths != {"/v1/*", "/healthz", "/readyz"}:
        raise AWSControlPlaneError("CloudFront API/probe paths do not match the profile")
    api = mapped["/v1/*"]
    allowed = api.get("AllowedMethods")
    allowed_items = allowed.get("Items") if isinstance(allowed, Mapping) else None
    if set(allowed_items or []) != {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}:
        raise AWSControlPlaneError("CloudFront API behavior does not allow the exact HTTP methods")
    api_cache_id = api.get("CachePolicyId")
    origin_policy_id = api.get("OriginRequestPolicyId")
    if not isinstance(api_cache_id, str) or not isinstance(origin_policy_id, str):
        raise AWSControlPlaneError("CloudFront API policies are absent")
    for path in ("/healthz", "/readyz"):
        behavior = mapped[path]
        if behavior.get("CachePolicyId") != api_cache_id:
            raise AWSControlPlaneError("CloudFront probes must use the zero-cache API policy")
        if behavior.get("OriginRequestPolicyId") != origin_policy_id:
            raise AWSControlPlaneError("CloudFront probes must use the API origin policy")
    default = config.get("DefaultCacheBehavior")
    static_cache_id = default.get("CachePolicyId") if isinstance(default, Mapping) else None
    if not isinstance(static_cache_id, str):
        raise AWSControlPlaneError("CloudFront static cache policy is absent")
    return api_cache_id, static_cache_id, origin_policy_id


def _verify_cloudfront_policies(
    source: AWSControlPlaneInput,
    *,
    api_cache_id: str,
    static_cache_id: str,
    origin_policy_id: str,
) -> None:
    api = _aws_json(
        source,
        "cloudfront",
        "get-cache-policy",
        "--id",
        api_cache_id,
    )
    config = _nested_mapping(api, "CachePolicy", "CachePolicyConfig")
    if any(config.get(name) != 0 for name in ("DefaultTTL", "MaxTTL", "MinTTL")):
        raise AWSControlPlaneError("CloudFront API cache policy must use zero TTLs")
    static = _aws_json(
        source,
        "cloudfront",
        "get-cache-policy",
        "--id",
        static_cache_id,
    )
    static_config = _nested_mapping(static, "CachePolicy", "CachePolicyConfig")
    if static_config.get("MinTTL") != 0:
        raise AWSControlPlaneError("CloudFront static policy must preserve origin no-store")
    origin = _aws_json(
        source,
        "cloudfront",
        "get-origin-request-policy",
        "--id",
        origin_policy_id,
    )
    origin_config = _nested_mapping(origin, "OriginRequestPolicy", "OriginRequestPolicyConfig")
    headers = _nested_mapping(origin_config, "HeadersConfig")
    cookies = _nested_mapping(origin_config, "CookiesConfig")
    query = _nested_mapping(origin_config, "QueryStringsConfig")
    if (
        headers.get("HeaderBehavior") != "allViewer"
        or cookies.get("CookieBehavior") != "none"
        or query.get("QueryStringBehavior") != "all"
    ):
        raise AWSControlPlaneError("CloudFront origin forwarding does not match the API contract")


def _verify_load_balancer(source: AWSControlPlaneInput, distribution_value: object) -> None:
    config = _nested_mapping(distribution_value, "Distribution", "DistributionConfig")
    origins = _nested_mapping(config, "Origins").get("Items")
    if not isinstance(origins, list) or len(origins) != 1 or not isinstance(origins[0], Mapping):
        raise AWSControlPlaneError("CloudFront must use exactly one ALB origin")
    origin_domain = origins[0].get("DomainName")
    load_balancers = _aws_json(
        source,
        "elbv2",
        "describe-load-balancers",
        "--names",
        f"{source.name}-d7-control",
    )
    items = load_balancers.get("LoadBalancers") if isinstance(load_balancers, Mapping) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
        raise AWSControlPlaneError("AWS profile ALB is absent")
    load_balancer = items[0]
    if (
        load_balancer.get("DNSName") != origin_domain
        or load_balancer.get("Scheme") != "internet-facing"
        or load_balancer.get("Type") != "application"
        or load_balancer.get("State", {}).get("Code") != "active"
    ):
        raise AWSControlPlaneError("AWS ALB identity or state does not match CloudFront")
    arn = load_balancer.get("LoadBalancerArn")
    if not isinstance(arn, str):
        raise AWSControlPlaneError("AWS ALB ARN is absent")
    attributes = _aws_json(
        source,
        "elbv2",
        "describe-load-balancer-attributes",
        "--load-balancer-arn",
        arn,
    )
    attribute_items = attributes.get("Attributes", []) if isinstance(attributes, Mapping) else []
    values = {
        item.get("Key"): item.get("Value") for item in attribute_items if isinstance(item, Mapping)
    }
    if values.get("access_logs.s3.enabled") != "false":
        raise AWSControlPlaneError("AWS ALB access logging must remain disabled")
    if values.get("deletion_protection.enabled") != "false":
        raise AWSControlPlaneError("AWS disposable ALB must not enable deletion protection")


def _verify_services(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or value.get("failures"):
        raise AWSControlPlaneError("AWS ECS services could not be read")
    services = value.get("services")
    if not isinstance(services, list) or len(services) != 2:
        raise AWSControlPlaneError("AWS profile requires exactly two ECS services")
    target_groups: dict[str, str] = {}
    for service in services:
        if (
            not isinstance(service, Mapping)
            or service.get("status") != "ACTIVE"
            or service.get("desiredCount") != 1
            or service.get("runningCount") != 1
            or service.get("pendingCount") != 0
        ):
            raise AWSControlPlaneError("AWS ECS service is not stable at one task")
        deployments = service.get("deployments")
        if not isinstance(deployments, list) or len(deployments) != 1:
            raise AWSControlPlaneError("AWS ECS service has a stale or split deployment")
        name = service.get("serviceName")
        load_balancers = service.get("loadBalancers")
        if not isinstance(name, str) or not isinstance(load_balancers, list):
            raise AWSControlPlaneError("AWS ECS service routing is absent")
        workload = "control" if name.endswith("-control") else "druff"
        if len(load_balancers) != 1 or not isinstance(load_balancers[0], Mapping):
            raise AWSControlPlaneError("AWS ECS service must use exactly one target group")
        target_group = load_balancers[0].get("targetGroupArn")
        if not isinstance(target_group, str):
            raise AWSControlPlaneError("AWS ECS target group ARN is absent")
        target_groups[workload] = target_group
    if set(target_groups) != {"control", "druff"}:
        raise AWSControlPlaneError("AWS ECS service names do not match the profile")
    return target_groups


def _verify_task(workload: str, image: str, value: object) -> str:
    tasks = value.get("tasks") if isinstance(value, Mapping) else None
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], Mapping):
        raise AWSControlPlaneError(f"AWS {workload} task response is malformed")
    task = tasks[0]
    if task.get("lastStatus") != "RUNNING" or task.get("healthStatus") not in {
        None,
        "UNKNOWN",
        "HEALTHY",
    }:
        raise AWSControlPlaneError(f"AWS {workload} task is not running")
    containers = task.get("containers")
    if not isinstance(containers, list):
        raise AWSControlPlaneError(f"AWS {workload} containers are absent")
    app = next(
        (item for item in containers if isinstance(item, Mapping) and item.get("name") == workload),
        None,
    )
    if not isinstance(app, Mapping) or app.get("image") != image:
        raise AWSControlPlaneError(f"AWS {workload} task does not run the selected digest")
    task_definition_arn = task.get("taskDefinitionArn")
    if not isinstance(task_definition_arn, str):
        raise AWSControlPlaneError(f"AWS {workload} task definition ARN is absent")
    return task_definition_arn


def _verify_task_definition(
    source: AWSControlPlaneInput,
    rendered: Mapping[str, str],
    *,
    workload: str,
    app_image: str,
    init_image: str,
    value: object,
) -> None:
    definition = _nested_mapping(value, "taskDefinition")
    compatibilities = definition.get("requiresCompatibilities")
    if (
        definition.get("status") != "ACTIVE"
        or definition.get("networkMode") != "awsvpc"
        or not isinstance(compatibilities, list)
        or "FARGATE" not in compatibilities
    ):
        raise AWSControlPlaneError(f"AWS {workload} task definition is not active Fargate")
    expected_role = (
        source.control_task_role_arn
        if workload == "control"
        else source.control_task_role_arn.replace("-control-task", "-druff-task")
    )
    if definition.get("taskRoleArn") != expected_role:
        raise AWSControlPlaneError(f"AWS {workload} task role does not match the projection")
    containers = definition.get("containerDefinitions")
    if not isinstance(containers, list) or len(containers) != 2:
        raise AWSControlPlaneError(f"AWS {workload} task must have init and app containers")
    by_name = {item.get("name"): item for item in containers if isinstance(item, Mapping)}
    init = by_name.get("config-init")
    app = by_name.get(workload)
    if not isinstance(init, Mapping) or not isinstance(app, Mapping):
        raise AWSControlPlaneError(f"AWS {workload} task containers are misnamed")
    if (
        init.get("image") != init_image
        or init.get("essential") is not False
        or init.get("readonlyRootFilesystem") is not True
        or init.get("user") != "0:0"
        or _capabilities(init) != {"add": [], "drop": ["ALL"]}
    ):
        raise AWSControlPlaneError(f"AWS {workload} config init boundary is invalid")
    depends_on = app.get("dependsOn")
    if (
        app.get("image") != app_image
        or app.get("essential") is not True
        or app.get("readonlyRootFilesystem") is not True
        or app.get("user") != "65532:65532"
        or _capabilities(app) != {"add": [], "drop": ["ALL"]}
        or depends_on != [{"containerName": "config-init", "condition": "SUCCESS"}]
    ):
        raise AWSControlPlaneError(f"AWS {workload} application boundary is invalid")
    active = json.loads(rendered["active.tfvars.json"])
    if not isinstance(active, Mapping):
        raise AWSControlPlaneError("AWS active projection is malformed")
    if workload == "control":
        projected_command = active.get("control_args")
        if not isinstance(projected_command, list) or not all(
            isinstance(part, str) for part in projected_command
        ):
            raise AWSControlPlaneError("AWS Control command projection is malformed")
        expected_command = list(projected_command)
        if source.trigger_specs:
            expected_command.extend(
                (
                    "--schedule-queue-url",
                    _schedule_queue_url(source, dead_letter=False),
                )
            )
        if app.get("command") != expected_command:
            raise AWSControlPlaneError("AWS Control command does not match the projection")
    mounts = app.get("mountPoints")
    if not isinstance(mounts, list):
        raise AWSControlPlaneError(f"AWS {workload} application mounts are absent")
    mount_by_path = {
        item.get("containerPath"): item for item in mounts if isinstance(item, Mapping)
    }
    if (
        mount_by_path.get("/etc/dander", {}).get("readOnly") is not True
        or mount_by_path.get("/tmp", {}).get("readOnly") is not False
    ):
        raise AWSControlPlaneError(f"AWS {workload} config/tmp mounts are invalid")
    environment = {
        item.get("name"): item.get("value")
        for item in init.get("environment", [])
        if isinstance(item, Mapping)
    }
    expected = (
        {
            "CONTROL_OIDC_B64": _b64(rendered["control-oidc.json"]),
            "GRAPH_STORE_B64": _b64(rendered["control-graph-store.json"]),
            "PLATFORMS_CONFIG_B64": _b64(str(active.get("platforms_config_yaml", ""))),
            "EXECUTION_PLANS_B64": _b64(
                _terraform_jsonencode(active.get("execution_plan_json", {}))
            ),
            "TRIGGER_SPECS_B64": _b64(_terraform_jsonencode(active.get("trigger_spec_json", {}))),
        }
        if workload == "control"
        else {
            "DRUFF_BOOTSTRAP_B64": _b64(rendered["bootstrap.json"]),
            "DRUFF_CADDY_B64": _b64(rendered["Caddyfile"]),
        }
    )
    if environment != expected:
        raise AWSControlPlaneError(f"AWS {workload} startup config does not match projection")


def _verify_scheduling(source: AWSControlPlaneInput) -> dict[str, object] | None:
    if not source.trigger_specs:
        return None
    partition = "aws-us-gov" if source.region.startswith("us-gov-") else "aws"
    queue_url = _schedule_queue_url(source, dead_letter=False)
    dlq_url = _schedule_queue_url(source, dead_letter=True)
    queue_arn = (
        f"arn:{partition}:sqs:{source.region}:{source.aws_account_id}:"
        f"{source.name}-d7-control-schedules"
    )
    dlq_arn = (
        f"arn:{partition}:sqs:{source.region}:{source.aws_account_id}:"
        f"{source.name}-d7-control-schedule-dlq"
    )
    queue = _aws_json(
        source,
        "sqs",
        "get-queue-attributes",
        "--queue-url",
        queue_url,
        "--attribute-names",
        "QueueArn",
        "SqsManagedSseEnabled",
        "RedrivePolicy",
        "ReceiveMessageWaitTimeSeconds",
        "VisibilityTimeout",
    )
    attributes = queue.get("Attributes") if isinstance(queue, Mapping) else None
    if not isinstance(attributes, Mapping):
        raise AWSControlPlaneError("AWS Control schedule queue attributes are absent")
    try:
        redrive = json.loads(str(attributes.get("RedrivePolicy")))
    except json.JSONDecodeError as error:
        raise AWSControlPlaneError("AWS Control schedule redrive policy is malformed") from error
    if (
        attributes.get("QueueArn") != queue_arn
        or attributes.get("SqsManagedSseEnabled") != "true"
        or attributes.get("ReceiveMessageWaitTimeSeconds") != "20"
        or attributes.get("VisibilityTimeout") != "120"
        or redrive
        != {
            "deadLetterTargetArn": dlq_arn,
            "maxReceiveCount": 5,
        }
    ):
        raise AWSControlPlaneError("AWS Control schedule queue does not match the projection")
    dlq = _aws_json(
        source,
        "sqs",
        "get-queue-attributes",
        "--queue-url",
        dlq_url,
        "--attribute-names",
        "QueueArn",
        "SqsManagedSseEnabled",
    )
    dlq_attributes = dlq.get("Attributes") if isinstance(dlq, Mapping) else None
    if not isinstance(dlq_attributes, Mapping) or dlq_attributes != {
        "QueueArn": dlq_arn,
        "SqsManagedSseEnabled": "true",
    }:
        raise AWSControlPlaneError("AWS Control schedule DLQ does not match the projection")
    role_arn = f"arn:{partition}:iam::{source.aws_account_id}:role/{source.name}-d7-scheduler"
    for spec in source.trigger_specs:
        schedule = _aws_json(
            source,
            "scheduler",
            "get-schedule",
            "--name",
            f"{source.name}-d7-schedule-{_sha256(spec.trigger_id)[:12]}",
        )
        if not isinstance(schedule, Mapping):
            raise AWSControlPlaneError(
                f"AWS Control schedule {spec.trigger_id} response is malformed"
            )
        target = schedule.get("Target")
        if (
            not isinstance(target, Mapping)
            or schedule.get("State") != ("ENABLED" if spec.enabled else "DISABLED")
            or schedule.get("ScheduleExpression") != spec.schedule
            or schedule.get("ScheduleExpressionTimezone") != spec.time_zone
            or schedule.get("FlexibleTimeWindow") != {"Mode": "OFF"}
            or target.get("Arn") != queue_arn
            or target.get("RoleArn") != role_arn
            or target.get("Input") != render_schedule_wakeup_template(spec)
            or target.get("DeadLetterConfig") != {"Arn": dlq_arn}
            or target.get("RetryPolicy")
            != {"MaximumEventAgeInSeconds": 3600, "MaximumRetryAttempts": 3}
        ):
            raise AWSControlPlaneError(
                f"AWS Control schedule {spec.trigger_id} does not match the projection"
            )
    return {
        "queue_arn": queue_arn,
        "dead_letter_arn": dlq_arn,
        "scheduled_trigger_ids": [spec.trigger_id for spec in source.trigger_specs],
    }


def _schedule_queue_url(source: AWSControlPlaneInput, *, dead_letter: bool) -> str:
    suffix = "control-schedule-dlq" if dead_letter else "control-schedules"
    return (
        f"https://sqs.{source.region}.amazonaws.com/{source.aws_account_id}/"
        f"{source.name}-d7-{suffix}"
    )


def _verify_target_health(workload: str, value: object) -> None:
    descriptions = value.get("TargetHealthDescriptions") if isinstance(value, Mapping) else None
    if (
        not isinstance(descriptions, list)
        or len(descriptions) != 1
        or not isinstance(descriptions[0], Mapping)
        or descriptions[0].get("TargetHealth", {}).get("State") != "healthy"
    ):
        raise AWSControlPlaneError(f"AWS {workload} target group is not exactly healthy")


def _nested_mapping(value: object, *keys: str) -> Mapping[str, object]:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            raise AWSControlPlaneError(f"AWS response is missing {'.'.join(keys)}")
        current = current.get(key)
    if not isinstance(current, Mapping):
        raise AWSControlPlaneError(f"AWS response is missing {'.'.join(keys)}")
    return current


def _capabilities(container: Mapping[str, object]) -> object:
    linux = container.get("linuxParameters")
    capabilities = linux.get("capabilities") if isinstance(linux, Mapping) else None
    if not isinstance(capabilities, Mapping):
        return None
    return {
        "add": capabilities.get("add", []),
        "drop": capabilities.get("drop", []),
    }


def _verify_bucket(source: AWSControlPlaneInput, bucket: str) -> None:
    versioning = _aws_json(
        source, "s3api", "get-bucket-versioning", "--bucket", bucket, regional=False
    )
    if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
        raise AWSControlPlaneError(f"AWS bucket {bucket} is not versioned")
    public = _aws_json(
        source, "s3api", "get-public-access-block", "--bucket", bucket, regional=False
    )
    settings = public.get("PublicAccessBlockConfiguration") if isinstance(public, Mapping) else None
    if not isinstance(settings, Mapping) or set(settings.values()) != {True}:
        raise AWSControlPlaneError(f"AWS bucket {bucket} is not fully public-blocked")
    encryption = _aws_json(
        source, "s3api", "get-bucket-encryption", "--bucket", bucket, regional=False
    )
    rules = (
        encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
        if isinstance(encryption, Mapping)
        else []
    )
    if not rules:
        raise AWSControlPlaneError(f"AWS bucket {bucket} has no default encryption")


def _aws_json(
    source: AWSControlPlaneFoundationInput,
    *arguments: str,
    regional: bool = True,
) -> object:
    command = ["aws", *arguments, "--output", "json", "--no-cli-pager"]
    if regional:
        command.extend(("--region", source.region))
    completed = _run(tuple(command))
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AWSControlPlaneError("AWS CLI returned malformed JSON") from error


def _http_json(url: str) -> object:
    payload, _headers = _http_bytes(url)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise AWSControlPlaneError(f"AWS endpoint returned malformed JSON: {url}") from error


def _http_bytes(url: str) -> tuple[bytes, dict[str, str]]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AWSControlPlaneError("AWS verifier accepts only absolute HTTPS URLs")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(6 * 1024 * 1024 + 1)
        headers = {name.casefold(): value for name, value in response.headers.items()}
    if len(payload) > 6 * 1024 * 1024:
        raise AWSControlPlaneError("AWS endpoint response exceeded the verifier bound")
    return payload, headers


def _run(
    command: Sequence[str], *, environment: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            tuple(command),
            check=True,
            capture_output=True,
            text=True,
            env=dict(environment) if environment is not None else None,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = (
            error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        )
        raise AWSControlPlaneError(f"command failed: {' '.join(command)}: {stderr}") from error


def _validate_rollback_pair(active: str, rollback: str, label: str) -> None:
    active_repository, active_digest = active.rsplit("@", 1)
    rollback_repository, rollback_digest = rollback.rsplit("@", 1)
    if active_repository != rollback_repository:
        raise ValueError(f"{label} active and rollback images must use the same repository.")
    if active_digest == rollback_digest:
        raise ValueError(f"{label} active and rollback digests must differ.")


def _digest(image: str) -> str:
    return image.rsplit("@", 1)[1]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def _terraform_jsonencode(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for character, escaped in (
        ("<", r"\u003c"),
        (">", r"\u003e"),
        ("&", r"\u0026"),
        ("\u2028", r"\u2028"),
        ("\u2029", r"\u2029"),
    ):
        encoded = encoded.replace(character, escaped)
    return encoded


def _load_input(path: Path) -> AWSControlPlaneFoundationInput | AWSControlPlaneInput:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AWSControlPlaneError("AWS input must be one JSON object")
    if "cloudfront_domain" in raw:
        return AWSControlPlaneInput.model_validate(raw)
    return AWSControlPlaneFoundationInput.model_validate(raw)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("render", "preflight"):
        child = subparsers.add_parser(name)
        child.add_argument("--input", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        if name == "preflight":
            child.add_argument("--terraform-root", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--input", type=Path, required=True)
    verify.add_argument("--environment", choices=("active", "rollback"), required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    source = _load_input(args.input)
    if args.command == "render":
        result: object = {
            "files": [
                str(path) for path in write_aws_control_plane(source, output_directory=args.output)
            ]
        }
    elif args.command == "preflight":
        result = preflight_aws_control_plane(
            source,
            output_directory=args.output,
            terraform_root=args.terraform_root,
        )
    else:
        if not isinstance(source, AWSControlPlaneInput):
            raise AWSControlPlaneError("live verification requires the complete AWS input")
        result = verify_live_aws_control_plane(source, environment=args.environment)
    print(_json(result), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
