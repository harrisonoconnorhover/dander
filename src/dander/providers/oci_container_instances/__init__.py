"""OCI Container Instances launcher configuration."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from dander.providers.oci_container_instances.config import (
    OciContainerInstancesLauncherConfig,
)
from dander.providers.oci_container_instances.controller import (
    OCI_EXECUTION_SCHEMA,
    OciExecution,
    OciLifecycleController,
    OciLifecycleError,
)

if TYPE_CHECKING:
    from dander.providers.oci_container_instances.operations import (
        OciContainerInstanceOperations,
        OciInvocation,
        OciOperationBinding,
        OciOperationError,
    )

_OPERATION_EXPORTS = frozenset(
    {
        "OciContainerInstanceOperations",
        "OciInvocation",
        "OciOperationBinding",
        "OciOperationError",
    }
)


def __getattr__(name: str) -> object:
    """Keep manifest/project operation dependencies out of OCI Function startup."""
    if name in _OPERATION_EXPORTS:
        module = import_module("dander.providers.oci_container_instances.operations")
        return getattr(module, name)
    raise AttributeError(name)


__all__ = [
    "OCI_EXECUTION_SCHEMA",
    "OciContainerInstancesLauncherConfig",
    "OciContainerInstanceOperations",
    "OciExecution",
    "OciLifecycleController",
    "OciLifecycleError",
    "OciInvocation",
    "OciOperationBinding",
    "OciOperationError",
]
