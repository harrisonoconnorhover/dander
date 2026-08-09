"""Dependency-light configuration for an existing Kubernetes cluster."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DNS_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_LABEL_NAME = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?/)?"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$"
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?)?$")


class KubernetesLauncherConfig(BaseModel):
    """Select one existing Kubernetes context and namespace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["kubernetes"]
    context: str = Field(min_length=1, max_length=253)
    namespace: str = Field(default="dander", min_length=1, max_length=63)
    release_name: str = Field(default="dander", min_length=1, max_length=53)
    service_account_name: str = Field(default="dander-runtime", min_length=1, max_length=63)
    existing_secret_name: str | None = Field(default=None, min_length=1, max_length=253)
    workload_identity_annotations: dict[str, str] = Field(default_factory=dict)
    pod_labels: dict[str, str] = Field(default_factory=dict)
    ephemeral_storage_mib: int = Field(default=1_024, ge=1, le=1_048_576)
    successful_jobs_history_limit: int = Field(default=3, ge=0, le=100)
    failed_jobs_history_limit: int = Field(default=3, ge=0, le=100)
    ttl_seconds_after_finished: int = Field(default=3_600, ge=60, le=604_800)
    termination_grace_period_seconds: int = Field(default=120, ge=1, le=600)

    @property
    def region(self) -> str:
        """Satisfy the shared launcher location surface without inventing a cloud region."""
        return "cluster"

    @field_validator(
        "namespace",
        "release_name",
        "service_account_name",
    )
    @classmethod
    def validate_dns_labels(cls, value: str) -> str:
        if _DNS_LABEL.fullmatch(value) is None:
            raise ValueError("Kubernetes names must be valid DNS labels")
        return value

    @field_validator("existing_secret_name")
    @classmethod
    def validate_secret_name(cls, value: str | None) -> str | None:
        if value is not None and _DNS_SUBDOMAIN.fullmatch(value) is None:
            raise ValueError("Kubernetes Secret names must be valid DNS subdomains")
        return value

    @field_validator("context")
    @classmethod
    def validate_context(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("Kubernetes context must not contain whitespace")
        return value

    @field_validator("workload_identity_annotations")
    @classmethod
    def validate_annotations(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            _LABEL_NAME.fullmatch(key) is None
            or len(value) > 4_096
            or "\n" in value
            or "\r" in value
            for key, value in values.items()
        ):
            raise ValueError("workload identity annotations are invalid")
        return dict(sorted(values.items()))

    @field_validator("pod_labels")
    @classmethod
    def validate_labels(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            _LABEL_NAME.fullmatch(key) is None
            or _LABEL_VALUE.fullmatch(value) is None
            or key.startswith(("app.kubernetes.io/", "dander.io/", "helm.sh/"))
            for key, value in values.items()
        ):
            raise ValueError("pod labels are invalid")
        return dict(sorted(values.items()))
