"""Hosted startup selection for closed GraphStore bindings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from dander.control.graph_store import GraphStore, InMemoryGraphStore
from dander.control.graph_store_factory import build_bound_graph_store
from dander.deployment.service import (
    AzureBlobGraphStoreBinding,
    GCSGraphStoreBinding,
    GraphStoreBinding,
    LocalGraphStoreBinding,
    OCIObjectGraphStoreBinding,
    S3GraphStoreBinding,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


@pytest.mark.parametrize(
    ("binding", "constructor_path", "expected_args", "expected_kwargs"),
    [
        (
            LocalGraphStoreBinding(root="/var/lib/dander/control"),
            "dander.control.local_graph_store.RootedLocalGraphStore",
            (Path("/var/lib/dander/control"),),
            {},
        ),
        (
            GCSGraphStoreBinding(bucket="dander-control-test", prefix="graphs/v1"),
            "dander.control.gcs_graph_store.GCSGraphStore",
            ("dander-control-test",),
            {"prefix": "graphs/v1"},
        ),
        (
            S3GraphStoreBinding(
                bucket="dander-control-test",
                prefix="graphs/v1",
                expected_bucket_owner="123456789012",
            ),
            "dander.control.s3_graph_store.S3GraphStore",
            ("dander-control-test",),
            {"prefix": "graphs/v1", "expected_bucket_owner": "123456789012"},
        ),
        (
            AzureBlobGraphStoreBinding(
                account_url="https://dandercontrol.blob.core.windows.net",
                container="graphs",
                prefix="graphs/v1",
            ),
            "dander.control.azure_blob_graph_store.AzureBlobGraphStore",
            ("https://dandercontrol.blob.core.windows.net", "graphs"),
            {"prefix": "graphs/v1"},
        ),
        (
            OCIObjectGraphStoreBinding(
                namespace="dander",
                bucket="graphs",
                prefix="graphs/v1",
            ),
            "dander.control.oci_object_graph_store.OCIObjectGraphStore",
            ("dander", "graphs"),
            {"prefix": "graphs/v1"},
        ),
    ],
)
def test_binding_selects_exact_adapter_without_reaching_its_default_client(
    binding: GraphStoreBinding,
    constructor_path: str,
    expected_args: tuple[object, ...],
    expected_kwargs: dict[str, object],
    monkeypatch: MonkeyPatch,
) -> None:
    observed: list[tuple[tuple[object, ...], dict[str, object]]] = []
    selected = InMemoryGraphStore()

    def capture(*args: object, **kwargs: object) -> GraphStore:
        observed.append((args, kwargs))
        return selected

    monkeypatch.setattr(constructor_path, capture)

    assert build_bound_graph_store(binding) is selected
    assert observed == [(expected_args, expected_kwargs)]


def test_factory_rejects_values_outside_the_closed_union() -> None:
    with pytest.raises(TypeError, match="unsupported GraphStore binding"):
        build_bound_graph_store(cast("GraphStoreBinding", object()))
