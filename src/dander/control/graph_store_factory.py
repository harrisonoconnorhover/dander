"""Typed startup selection for hosted Control GraphStore bindings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dander.deployment.service import (
    AzureBlobGraphStoreBinding,
    GCSGraphStoreBinding,
    GraphStoreBinding,
    LocalGraphStoreBinding,
    OCIObjectGraphStoreBinding,
    S3GraphStoreBinding,
)

if TYPE_CHECKING:
    from dander.control.graph_store import GraphStore


def build_bound_graph_store(binding: GraphStoreBinding) -> GraphStore:
    """Instantiate only the selected credential-free runtime locator.

    Provider adapter modules and their default credential chains are reached only after the
    complete JSON locator has been parsed and validated into one closed binding arm.
    """
    if isinstance(binding, LocalGraphStoreBinding):
        from dander.control.local_graph_store import RootedLocalGraphStore

        return RootedLocalGraphStore(Path(binding.root))
    if isinstance(binding, GCSGraphStoreBinding):
        from dander.control.gcs_graph_store import GCSGraphStore

        return GCSGraphStore(binding.bucket, prefix=binding.prefix)
    if isinstance(binding, S3GraphStoreBinding):
        from dander.control.s3_graph_store import S3GraphStore

        return S3GraphStore(
            binding.bucket,
            prefix=binding.prefix,
            expected_bucket_owner=binding.expected_bucket_owner,
        )
    if isinstance(binding, AzureBlobGraphStoreBinding):
        from dander.control.azure_blob_graph_store import AzureBlobGraphStore

        return AzureBlobGraphStore(
            binding.account_url,
            binding.container,
            prefix=binding.prefix,
        )
    if isinstance(binding, OCIObjectGraphStoreBinding):
        from dander.control.oci_object_graph_store import OCIObjectGraphStore

        return OCIObjectGraphStore(
            binding.namespace,
            binding.bucket,
            prefix=binding.prefix,
        )
    raise TypeError("unsupported GraphStore binding")


__all__ = ["build_bound_graph_store"]
