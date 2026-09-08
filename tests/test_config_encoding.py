"""Configuration files with the wrong encoding produce actionable domain errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dander.ingestion.config import ConnectorConfigError, load_source_config
from dander.pipeline.runtime import GraphRuntimeError, load_graph_for_execution
from dander.project.config import ProjectConfigError, load_project_config
from dander.transform.config import TransformConfigError, load_model_metadata

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.parametrize(
    ("loader", "error_type"),
    [
        (load_source_config, ConnectorConfigError),
        (load_project_config, ProjectConfigError),
        (load_graph_for_execution, GraphRuntimeError),
        (load_model_metadata, TransformConfigError),
    ],
    ids=("connector", "project", "graph", "model"),
)
def test_non_utf8_configuration_reports_how_to_fix_the_file(
    tmp_path: Path,
    loader: Callable[[Path], object],
    error_type: type[Exception],
) -> None:
    path = tmp_path / "config.yaml"
    path.write_bytes("name: example\n".encode("utf-16"))

    with pytest.raises(error_type, match="UTF-8") as raised:
        loader(path)

    assert path.name in str(raised.value)
