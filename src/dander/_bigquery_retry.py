"""Narrow retry support for BigQuery transaction contention."""

from __future__ import annotations

import logging
import random
from time import sleep
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)
_CONCURRENT_UPDATE_MESSAGES = (
    "transaction is aborted due to concurrent update against table",
    "could not serialize access to table",
)
_MAX_ATTEMPTS = 5
_BASE_DELAY_SECONDS = 0.25


class _Job(Protocol):
    def result(self) -> object:
        """Wait for the submitted BigQuery job."""


def run_mutation_with_retry[JobT: _Job](submit: Callable[[], JobT]) -> JobT:
    """Run a mutation, retrying only BigQuery's concurrent-update transaction abort."""
    from google.api_core.exceptions import BadRequest

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            job = submit()
            job.result()
            return job
        except BadRequest as error:
            message = str(error).lower()
            concurrent_update = any(
                signature in message for signature in _CONCURRENT_UPDATE_MESSAGES
            )
            if not concurrent_update or attempt == _MAX_ATTEMPTS:
                raise
            delay = (_BASE_DELAY_SECONDS * (2 ** (attempt - 1))) + random.uniform(
                0,
                _BASE_DELAY_SECONDS,
            )
            _LOGGER.warning(
                "BigQuery transaction contention; retrying mutation in %.2fs (attempt %d/%d)",
                delay,
                attempt + 1,
                _MAX_ATTEMPTS,
            )
            sleep(delay)
    raise AssertionError("BigQuery mutation retry loop exited unexpectedly")
