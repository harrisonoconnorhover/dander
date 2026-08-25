"""AWS SQS transport for scheduled Control wakeups."""

from __future__ import annotations

import re
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from dander.control.schedule_consumer import (
    QueuedScheduleMessage,
    ScheduleQueueError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_ACCOUNT = re.compile(r"^[0-9]{12}$")
_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_QUEUE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class _SQSClient(Protocol):
    def receive_message(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_message(self, **kwargs: object) -> object: ...

    def close(self) -> object: ...


class SQSScheduleQueue:
    """Receive/delete one standard SQS queue using ambient AWS workload identity."""

    def __init__(
        self,
        queue_url: str,
        *,
        expected_account_id: str,
        expected_region: str,
        client: _SQSClient | None = None,
    ) -> None:
        _validate_queue_url(
            queue_url,
            expected_account_id=expected_account_id,
            expected_region=expected_region,
        )
        self._queue_url = queue_url
        self._region = expected_region
        self._client = client
        self._closed = False

    def receive(self) -> tuple[QueuedScheduleMessage, ...]:
        """Long-poll one bounded batch without acknowledging it."""
        try:
            response = self._require_client().receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
            )
            raw_messages = response.get("Messages", [])
            if not isinstance(raw_messages, list) or len(raw_messages) > 10:
                raise ScheduleQueueError("Schedule queue returned an invalid batch.")
            messages: list[QueuedScheduleMessage] = []
            for raw in raw_messages:
                if not isinstance(raw, dict):
                    raise ScheduleQueueError("Schedule queue returned an invalid message.")
                receipt = raw.get("ReceiptHandle")
                body = raw.get("Body")
                if not isinstance(receipt, str) or not isinstance(body, str):
                    raise ScheduleQueueError("Schedule queue returned an invalid message.")
                messages.append(
                    QueuedScheduleMessage(
                        receipt_handle=receipt,
                        body=body.encode("utf-8"),
                    )
                )
            return tuple(messages)
        except ScheduleQueueError:
            raise
        except Exception as error:  # noqa: BLE001 - sanitize botocore/provider failures
            raise ScheduleQueueError("Schedule queue receive failed.") from error

    def delete(self, receipt_handle: str) -> None:
        """Acknowledge one occurrence only after durable Control handoff succeeds."""
        if not receipt_handle or len(receipt_handle) > 4096:
            raise ScheduleQueueError("Schedule queue receipt is invalid.")
        try:
            self._require_client().delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
        except ScheduleQueueError:
            raise
        except Exception as error:  # noqa: BLE001 - sanitize botocore/provider failures
            raise ScheduleQueueError("Schedule queue delete failed.") from error

    def close(self) -> None:
        """Close the constructed or injected SDK client idempotently."""
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            try:
                self._client.close()
            except Exception as error:  # noqa: BLE001 - sanitize botocore/provider failures
                raise ScheduleQueueError("Schedule queue transport close failed.") from error

    def _require_client(self) -> _SQSClient:
        if self._closed:
            raise ScheduleQueueError("Schedule queue is closed.")
        if self._client is None:
            try:
                boto3 = import_module("boto3")
                config_module = import_module("botocore.config")
                config = config_module.Config(
                    connect_timeout=5,
                    read_timeout=25,
                    retries={"max_attempts": 3, "mode": "standard"},
                )
                self._client = cast(
                    "_SQSClient",
                    boto3.client("sqs", region_name=self._region, config=config),
                )
            except Exception as error:  # noqa: BLE001 - sanitize dependency/provider failures
                raise ScheduleQueueError("Schedule queue client is unavailable.") from error
        return self._client


def _validate_queue_url(
    value: str,
    *,
    expected_account_id: str,
    expected_region: str,
) -> None:
    if (
        _ACCOUNT.fullmatch(expected_account_id) is None
        or _REGION.fullmatch(expected_region) is None
    ):
        raise ScheduleQueueError("Schedule queue AWS coordinates are invalid.")
    parsed = urlsplit(value)
    expected_host = f"sqs.{expected_region}.amazonaws.com"
    parts = parsed.path.removeprefix("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or parts[0] != expected_account_id
        or _QUEUE_NAME.fullmatch(parts[1]) is None
    ):
        raise ScheduleQueueError("Schedule queue URL does not match the selected AWS boundary.")


__all__ = ["SQSScheduleQueue"]
