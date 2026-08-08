"""Provider-neutral durable-state runtime composition."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from dander.catalog import MetadataStore
    from dander.state.lease import LeaseStore
    from dander.state.run_history import RunHistoryStore
    from dander.state.watermark import WatermarkStore

_MIGRATION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class StateMigration:
    """One monotonically versioned durable-state schema change."""

    version: int
    name: str

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise ValueError("state migration version must be positive")
        if not _MIGRATION_NAME.fullmatch(self.name):
            raise ValueError("state migration name must be a safe lowercase identifier")


class StateMigrator(Protocol):
    """Apply a provider's ordered, idempotent durable-state migrations."""

    @property
    def migrations(self) -> tuple[StateMigration, ...]:
        """Return every migration understood by this runtime."""

    def current_version(self) -> int:
        """Return the greatest successfully applied migration version."""

    def migrate(self) -> int:
        """Apply outstanding migrations and return the resulting schema version."""


@dataclass(frozen=True, slots=True)
class StateCapabilities:
    """Correctness guarantees exposed by one durable-state provider."""

    provider_id: str
    schema_version: int
    server_time: bool
    atomic_leases: bool
    monotonic_fencing: bool
    atomic_watermark_cas: bool
    interrupted_run_reconciliation: bool

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("state capabilities require a provider id")
        if self.schema_version <= 0:
            raise ValueError("state schema version must be positive")


@dataclass(frozen=True, slots=True)
class StateRuntime:
    """The durable control-plane stores selected by one platform profile."""

    provider_id: str
    leases: LeaseStore
    watermarks: WatermarkStore
    history: RunHistoryStore
    metadata: MetadataStore | None
    migrator: StateMigrator
    capabilities: StateCapabilities

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("state runtime requires a provider id")
        if self.capabilities.provider_id != self.provider_id:
            raise ValueError("state runtime and capabilities provider ids must match")
        latest = max(migration.version for migration in self.migrator.migrations)
        if latest != self.capabilities.schema_version:
            raise ValueError("state runtime migration and capability versions must match")
