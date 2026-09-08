"""Loading and validation for connector YAML files."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from dander.ingestion.source import SourceConfig

if TYPE_CHECKING:
    from pathlib import Path


class ConnectorConfigError(ValueError):
    """Raised when a connector file cannot be loaded as `SourceConfig`."""


def load_source_config(path: Path) -> SourceConfig:
    """Load and validate a connector YAML file.

    Args:
        path: YAML file containing one declarative source.

    Returns:
        The validated source configuration.

    Raises:
        ConnectorConfigError: If the file is missing, malformed, empty, or invalid.
    """
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise ConnectorConfigError(f"Connector config must use UTF-8: {path}") from error
    except (OSError, yaml.YAMLError) as error:
        raise ConnectorConfigError(f"Could not load connector config {path}: {error}") from error

    if payload is None:
        raise ConnectorConfigError(f"Connector config {path} is empty")

    try:
        return SourceConfig.model_validate(payload)
    except ValidationError as error:
        raise ConnectorConfigError(f"Connector config {path} is invalid: {error}") from error
