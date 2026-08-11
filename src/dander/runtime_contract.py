"""Stable, non-sensitive output contract for the Dander OCI runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from dander.executor import PipelineExecutionResult

RUNTIME_CONTRACT = "io.dander.runtime/v1"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RuntimeContractError(ValueError):
    """Raised when a launcher supplies an invalid runtime-contract value."""


def resolve_runtime_run_id(value: str | None) -> str:
    """Return a launcher-provided run id or create an opaque local id."""
    run_id = value if value is not None else uuid4().hex
    if not _RUN_ID.fullmatch(run_id):
        raise RuntimeContractError(
            "run id must be 1-128 characters using letters, numbers, '.', '_', ':', or '-'"
        )
    return run_id


@dataclass(frozen=True, slots=True)
class RuntimeOutcome:
    """One JSON-serializable terminal record emitted by the OCI runtime."""

    status: Literal["succeeded", "skipped", "failed"]
    run_id: str
    pipeline_id: str
    outputs: dict[str, object]
    error_code: str | None = None

    @classmethod
    def completed(cls, result: PipelineExecutionResult) -> RuntimeOutcome:
        """Build the terminal contract record for a completed executor run."""
        endpoints = [
            {
                "name": endpoint.endpoint,
                "extracted_rows": endpoint.extracted,
                "affected_rows": endpoint.affected,
                "cursor_committed": endpoint.committed_cursor is not None,
            }
            for endpoint in result.ingestion.endpoints
        ]
        return cls(
            status="skipped" if result.skipped else "succeeded",
            run_id=result.run_id,
            pipeline_id=result.pipeline_id,
            outputs={
                "source": result.ingestion.source,
                "endpoints": endpoints,
                "models": list(result.models),
                "metrics": {
                    "endpoints": len(endpoints),
                    "extracted_rows": sum(
                        endpoint.extracted for endpoint in result.ingestion.endpoints
                    ),
                    "affected_rows": sum(
                        endpoint.affected for endpoint in result.ingestion.endpoints
                    ),
                    "models": len(result.models),
                    "assertions": result.assertions,
                    "assets": result.assets,
                },
            },
        )

    @classmethod
    def failed(cls, *, run_id: str, pipeline_id: str) -> RuntimeOutcome:
        """Build a deliberately terse failure record; details remain in run history and logs."""
        return cls(
            status="failed",
            run_id=run_id,
            pipeline_id=pipeline_id,
            outputs={},
            error_code="runtime_failed",
        )

    def to_json(self) -> str:
        """Render one compact JSON line suitable for log processors and launchers."""
        payload: dict[str, object] = {
            "contract": RUNTIME_CONTRACT,
            "event": "runtime.completed",
            "status": self.status,
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "outputs": self.outputs,
        }
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)
