"""Fail-closed sandbox safety tests for DANDER-21."""

from __future__ import annotations

import pytest

from dander.sandbox import (
    GcpBillingVerifier,
    GuardedFreeTierVerifier,
    SandboxDataset,
    SandboxSafetyError,
)


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _Session:
    def __init__(self, response: _Response | Exception) -> None:
        self._response = response

    def get(self, url: str, *, timeout: float) -> _Response:
        assert url.endswith("/projects/unit-project/billingInfo")
        assert timeout == 15.0
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _SequenceSession:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = iter(responses)
        self.urls: list[str] = []

    def get(self, url: str, *, timeout: float) -> _Response:
        assert timeout == 15.0
        self.urls.append(url)
        return next(self._responses)


class _Verifier:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    def require_disabled(self, project: str) -> None:
        self._events.append(f"verify:{project}")
        if self._fail:
            raise SandboxSafetyError("billing enabled")


class _Client:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def create_dataset(self, dataset: object, *, exists_ok: bool = False) -> object:
        self._events.append(f"create:{dataset}")
        assert exists_ok
        return dataset


def test_billing_verifier_accepts_only_explicit_disabled_response() -> None:
    verifier = GcpBillingVerifier(_Session(_Response(200, {"billingEnabled": False})))

    verifier.require_disabled("unit-project")


def test_billing_verifier_uses_launcher_identity_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = object()
    session = _Session(_Response(200, {"billingEnabled": False}))
    monkeypatch.setattr(
        "dander.sandbox.google_client_options",
        lambda: {"credentials": credentials},
    )
    monkeypatch.setattr(
        "dander.sandbox.google.auth.default",
        lambda **_kwargs: pytest.fail("ambient credentials must not be used"),
    )
    monkeypatch.setattr(
        "dander.sandbox.AuthorizedSession",
        lambda value: session if value is credentials else pytest.fail("wrong credentials"),
    )

    GcpBillingVerifier().require_disabled("unit-project")


@pytest.mark.parametrize(
    "response",
    [
        _Response(200, {"billingEnabled": True}),
        _Response(200, {"billingEnabled": "false"}),
        _Response(200, {}),
        _Response(403, {}),
        RuntimeError("offline"),
    ],
)
def test_billing_verifier_fails_closed(response: _Response | Exception) -> None:
    verifier = GcpBillingVerifier(_Session(response))

    with pytest.raises(SandboxSafetyError):
        verifier.require_disabled("unit-project")


def test_dataset_is_created_only_after_billing_verification() -> None:
    events: list[str] = []
    environment = SandboxDataset(
        verifier=_Verifier(events),
        client=_Client(events),
    )

    environment.prepare("unit-project", "raw")

    assert events[0] == "verify:unit-project"
    assert events[1].startswith("create:")


def test_failed_billing_check_prevents_dataset_creation() -> None:
    events: list[str] = []
    environment = SandboxDataset(
        verifier=_Verifier(events, fail=True),
        client=_Client(events),
    )

    with pytest.raises(SandboxSafetyError, match="billing enabled"):
        environment.prepare("unit-project", "raw")

    assert events == ["verify:unit-project"]


def _billing(*, enabled: bool = True) -> _Response:
    return _Response(
        200,
        {
            "billingEnabled": enabled,
            "billingAccountName": "billingAccounts/ABC-123",
        },
    )


def _budget(
    *,
    amount: str = "5",
    topic: str = "projects/unit-project/topics/dander-stop-billing",
) -> _Response:
    return _Response(
        200,
        {
            "budgets": [
                {
                    "displayName": "dander-sbx-cap",
                    "amount": {
                        "specifiedAmount": {
                            "currencyCode": "USD",
                            "units": amount,
                        }
                    },
                    "thresholdRules": [
                        {"thresholdPercent": 0.8, "spendBasis": "CURRENT_SPEND"},
                        {"thresholdPercent": 1.0, "spendBasis": "CURRENT_SPEND"},
                    ],
                    "notificationsRule": {"pubsubTopic": topic},
                }
            ]
        },
    )


def _subscription(*, attached: bool = True) -> _Response:
    subscriptions = (
        ["projects/unit-project/subscriptions/provider-managed-trigger"] if attached else []
    )
    return _Response(200, {"subscriptions": subscriptions})


def test_guarded_free_tier_accepts_complete_five_dollar_guard() -> None:
    session = _SequenceSession([_billing(), _budget(), _subscription()])

    GuardedFreeTierVerifier(session).require_guarded("unit-project")

    assert "scope=projects/unit-project" in session.urls[1]
    assert session.urls[2].endswith(
        "projects/unit-project/topics/dander-stop-billing/subscriptions?pageSize=100"
    )


def test_guarded_free_tier_uses_launcher_identity_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = object()
    session = _SequenceSession([_billing(), _budget(), _subscription()])
    monkeypatch.setattr(
        "dander.sandbox.google_client_options",
        lambda: {"credentials": credentials},
    )
    monkeypatch.setattr(
        "dander.sandbox.google.auth.default",
        lambda **_kwargs: pytest.fail("ambient credentials must not be used"),
    )
    monkeypatch.setattr(
        "dander.sandbox.AuthorizedSession",
        lambda value: session if value is credentials else pytest.fail("wrong credentials"),
    )

    GuardedFreeTierVerifier().require_guarded("unit-project")


@pytest.mark.parametrize(
    ("billing", "budget", "subscription", "message"),
    [
        (_billing(enabled=False), _budget(), _subscription(), "not enabled"),
        (_billing(), _budget(amount="6"), _subscription(), "no greater than"),
        (
            _billing(),
            _budget(topic="projects/unit-project/topics/wrong"),
            _subscription(),
            "must publish",
        ),
        (_billing(), _budget(), _subscription(attached=False), "kill-switch subscription"),
    ],
)
def test_guarded_free_tier_fails_closed(
    billing: _Response,
    budget: _Response,
    subscription: _Response,
    message: str,
) -> None:
    verifier = GuardedFreeTierVerifier(
        _SequenceSession([billing, budget, subscription]),
    )

    with pytest.raises(SandboxSafetyError, match=message):
        verifier.require_guarded("unit-project")
