"""Dependency-light Redshift provider registration surface."""

from dander.providers.redshift.config import RedshiftWarehouseConfig
from dander.providers.redshift.errors import RedshiftConnectionUnavailableError

__all__ = ["RedshiftConnectionUnavailableError", "RedshiftWarehouseConfig"]
