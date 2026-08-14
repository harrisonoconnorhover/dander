"""State module: watermark / control tracking for idempotent restarts."""

from dander.state.failure import (
    FailureDetails,
    classify_failure,
    failure_diagnostic_checkpoint,
    failure_diagnostic_was_logged_since,
    mark_failure_diagnostic_logged,
)
from dander.state.lease import (
    BigQueryLeaseStore,
    LeaseHandle,
    LeaseHeartbeat,
    LeaseLostError,
    LeaseStore,
    SqliteLeaseStore,
)
from dander.state.run_history import (
    BigQueryRunHistoryStore,
    RunHistoryStore,
    RunRecord,
    RunStage,
    RunStatus,
    SqliteRunHistoryStore,
)
from dander.state.runtime import (
    StateCapabilities,
    StateMigration,
    StateMigrator,
    StateRuntime,
)
from dander.state.watermark import BigQueryWatermarkStore, SqliteWatermarkStore, WatermarkStore

__all__ = [
    "BigQueryRunHistoryStore",
    "BigQueryWatermarkStore",
    "BigQueryLeaseStore",
    "LeaseHandle",
    "LeaseHeartbeat",
    "LeaseLostError",
    "LeaseStore",
    "FailureDetails",
    "RunHistoryStore",
    "RunRecord",
    "RunStage",
    "RunStatus",
    "SqliteRunHistoryStore",
    "SqliteLeaseStore",
    "SqliteWatermarkStore",
    "StateCapabilities",
    "StateMigration",
    "StateMigrator",
    "StateRuntime",
    "WatermarkStore",
    "classify_failure",
    "failure_diagnostic_checkpoint",
    "failure_diagnostic_was_logged_since",
    "mark_failure_diagnostic_logged",
]
