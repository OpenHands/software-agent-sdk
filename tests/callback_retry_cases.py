"""Shared completion-callback cases for both workspace senders.

Each sender test builds its own workspace and exits through ``with``. The
clock replaces the helper's sleep and monotonic time so backoff assertions do
not depend on a quiet CI machine. The HTTP request itself stays on a real
loopback socket.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import pytest

from tests.callback_http import Action, CallbackServer


_CALLBACK = "openhands.sdk.workspace.completion_callback"
_BACKOFF_BASE = 0.25
_BACKOFF_CAP = 2.0
_JITTER = 0.2
_TYPE_ONLY = re.compile(
    r"Completion callback failed \([A-Z]+\): [A-Za-z_][A-Za-z0-9_]*"
)


class FakeClock:
    """Monotonic clock that advances only when the helper sleeps."""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.slept.append(delay)
        self.now += delay


@dataclass(frozen=True)
class RetryCase:
    name: str
    actions: tuple[Action, ...]
    posts: int
    warnings: int = 0
    warning_has: str = ""
    info_has: str = ""
    forbid_sent: bool = False
    type_only: bool = False
    # 1-based attempt numbers whose default backoff should be slept.
    sleeps: tuple[int, ...] = ()
    # When set, the helper must sleep these exact seconds and nothing else.
    exact: tuple[float, ...] | None = None
    timeout: float | None = None
    budget: float | None = None
    backoff: float | None = None
    stall_seconds: float = 1.0


def _c(
    name: str,
    actions: tuple[Action, ...],
    posts: int,
    **kwargs: Any,
) -> RetryCase:
    return RetryCase(name=name, actions=actions, posts=posts, **kwargs)


RETRY_CASES: tuple[RetryCase, ...] = (
    _c(
        "retries_502_then_200",
        (502, 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "retries_429_and_503_then_201",
        (429, 503, 201),
        3,
        info_has="Completion callback sent (COMPLETED): 201",
        sleeps=(1, 2),
    ),
    _c(
        "first_204_is_one_post",
        (204,),
        1,
        info_has="Completion callback sent (COMPLETED): 204",
        exact=(),
    ),
    _c(
        "conflict_409_stops",
        (409,),
        1,
        info_has="Completion callback already terminal (COMPLETED): 409",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "conflict_after_502_stops",
        (502, 409),
        2,
        info_has="already terminal",
        forbid_sent=True,
        sleeps=(1,),
    ),
    _c(
        "status_401",
        (401, 200),
        1,
        warnings=1,
        warning_has="401",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "status_403",
        (403, 200),
        1,
        warnings=1,
        warning_has="403",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "status_404",
        (404, 200),
        1,
        warnings=1,
        warning_has="404",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "status_422",
        (422, 200),
        1,
        warnings=1,
        warning_has="422",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "transport_drop_then_200",
        ("drop", 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "read_timeout_then_200",
        ("stall", 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
        timeout=0.2,
    ),
    _c(
        "transport_drop_stops_at_attempt_cap",
        ("drop", "drop", "drop", "drop", 200),
        4,
        warnings=1,
        type_only=True,
        forbid_sent=True,
        sleeps=(1, 2, 3),
    ),
    _c(
        "server_error_stops_at_attempt_cap",
        (502, 502, 502, 502, 200),
        4,
        warnings=1,
        warning_has="502",
        forbid_sent=True,
        sleeps=(1, 2, 3),
    ),
    _c(
        "retry_after_120_does_not_fit",
        ((429, {"Retry-After": "120"}), 200),
        1,
        warnings=1,
        warning_has="429",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "retry_after_0_does_not_use_backoff",
        ((503, {"Retry-After": "0"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        exact=(),
    ),
    _c(
        "retry_after_one_second",
        ((503, {"Retry-After": "1"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        exact=(1.0,),
    ),
    _c(
        "past_http_date_retries_immediately",
        ((429, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        exact=(),
    ),
    _c(
        "future_http_date_does_not_fit",
        ((429, {"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}), 200),
        1,
        warnings=1,
        warning_has="429",
        forbid_sent=True,
        exact=(),
    ),
    _c(
        "nan_retry_after_uses_backoff",
        ((503, {"Retry-After": "NaN"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "infinity_retry_after_uses_backoff",
        ((503, {"Retry-After": "Infinity"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "exponent_retry_after_uses_backoff",
        ((503, {"Retry-After": "1e2"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "decimal_retry_after_uses_backoff",
        ((503, {"Retry-After": "1.5"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "signed_retry_after_uses_backoff",
        ((503, {"Retry-After": "+5"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "out_of_range_http_date_uses_backoff",
        ((503, {"Retry-After": "Wed, 21 Oct 10000 07:28:00 GMT"}), 200),
        2,
        info_has="Completion callback sent (COMPLETED): 200",
        sleeps=(1,),
    ),
    _c(
        "budget_stops_before_attempt_cap",
        (502, 502, 502, 502),
        1,
        warnings=1,
        warning_has="502",
        forbid_sent=True,
        exact=(),
        budget=1.0,
        backoff=5.0,
    ),
    _c(
        "read_timeout_stops_when_backoff_does_not_fit",
        ("stall", 200),
        1,
        warnings=1,
        type_only=True,
        forbid_sent=True,
        exact=(),
        budget=0.2,
    ),
)


def begin_case(monkeypatch: pytest.MonkeyPatch, case: RetryCase) -> FakeClock:
    if case.timeout is not None:
        monkeypatch.setattr(
            f"{_CALLBACK}.COMPLETION_CALLBACK_TIMEOUT_SECONDS", case.timeout
        )
    if case.budget is not None:
        monkeypatch.setattr(
            f"{_CALLBACK}.COMPLETION_CALLBACK_BUDGET_SECONDS", case.budget
        )
    if case.backoff is not None:
        monkeypatch.setattr(
            f"{_CALLBACK}.COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS",
            case.backoff,
        )
    clock = FakeClock()
    monkeypatch.setattr(f"{_CALLBACK}._monotonic", clock.monotonic)
    monkeypatch.setattr(f"{_CALLBACK}._sleep", clock.sleep)
    monkeypatch.setattr(f"{_CALLBACK}._wall_time", clock.time)
    return clock


def assert_case(
    case: RetryCase,
    server: CallbackServer,
    caplog: pytest.LogCaptureFixture,
    clock: FakeClock,
    secret: str,
) -> None:
    assert len(server.bodies) == case.posts
    if case.posts > 1:
        assert all(body == server.bodies[0] for body in server.bodies)
    assert server.authorization == [f"Bearer {secret}"] * case.posts
    own_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("openhands.")
    )
    assert secret not in own_logs
    assert "127.0.0.1" not in own_logs
    assert "http://" not in own_logs
    assert "Bearer " not in own_logs

    messages = [record.getMessage() for record in caplog.records]
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert len(warnings) == case.warnings
    if case.type_only:
        assert _TYPE_ONLY.fullmatch(warnings[0])
    elif case.warning_has:
        assert case.warning_has in warnings[0]
        assert "sent" not in warnings[0]
    if case.info_has:
        assert any(case.info_has in message for message in messages)
    if case.forbid_sent:
        assert all("sent" not in message for message in messages)

    if case.exact is not None:
        assert clock.slept == list(case.exact), clock.slept
        return
    assert len(clock.slept) == len(case.sleeps)
    for slept, attempt in zip(clock.slept, case.sleeps, strict=True):
        growth = _BACKOFF_BASE * (2 ** (attempt - 1))
        base = min(growth, _BACKOFF_CAP)
        high = base * (1 + _JITTER)
        assert base - 1e-9 <= slept <= high + 1e-9
