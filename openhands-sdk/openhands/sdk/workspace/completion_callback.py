"""Bounded retry for the automation completion callback.

Both workspace senders call this so status handling cannot drift. The payload,
headers, and the decision to skip a missing URL stay with the caller. This
function never raises: a lost callback must not replace the ``__exit__`` error.

At most four attempts are started. The 30 second monotonic budget only decides
whether another attempt may start and whether a backoff still fits. It does
not cancel a request that has already started. Each attempt uses httpx's sync
timeout, which is a per-phase idle limit (connect, write, and read), capped
at 10 seconds and at the time still left in the budget.
"""

from __future__ import annotations

import logging
import math
import random
import re
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


COMPLETION_CALLBACK_MAX_ATTEMPTS = 4
COMPLETION_CALLBACK_BUDGET_SECONDS = 30.0
COMPLETION_CALLBACK_TIMEOUT_SECONDS = 10.0
COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS = 0.25
_BACKOFF_CAP_SECONDS = 2.0
_JITTER_FRACTION = 0.2
# HTTP delta-seconds here are a non-negative integer. Float parsing would
# accept NaN, Infinity, and values such as "1e2".
_INTEGER_SECONDS = re.compile(r"^[0-9]+$")


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _wall_time() -> float:
    return time.time()


def post_completion_callback(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    status: str,
    logger: logging.Logger,
) -> None:
    """POST ``payload`` once per attempt. Never raises.

    Retries ``httpx.TransportError``, 429, and 5xx within the attempt cap and
    the total budget. Any 2xx is success. 409 means the run is already
    terminal, so it stops without a failure warning. Other 4xx responses are
    not retried. Logs name an exception type or a status code, not the
    request or the exception text.
    """
    deadline = _monotonic() + COMPLETION_CALLBACK_BUDGET_SECONDS
    try:
        with httpx.Client() as client:
            for attempt in range(1, COMPLETION_CALLBACK_MAX_ATTEMPTS + 1):
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    _warn(logger, status, "retry budget exhausted")
                    return
                timeout = min(COMPLETION_CALLBACK_TIMEOUT_SECONDS, remaining)
                try:
                    response = client.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                    )
                except httpx.TransportError as exc:
                    if attempt >= COMPLETION_CALLBACK_MAX_ATTEMPTS or not _pause(
                        attempt, None, deadline
                    ):
                        _warn(logger, status, _safe_error(exc))
                        return
                    continue

                kind = _classify(response.status_code)
                if kind == "success":
                    logger.info(
                        "Completion callback sent (%s): %s",
                        status,
                        response.status_code,
                    )
                    return
                if kind == "terminal":
                    logger.info(
                        "Completion callback already terminal (%s): %s",
                        status,
                        response.status_code,
                    )
                    return
                retry_after = response.headers.get("Retry-After")
                if kind == "retry" and attempt < COMPLETION_CALLBACK_MAX_ATTEMPTS:
                    if _pause(attempt, retry_after, deadline):
                        continue
                _warn(logger, status, response.status_code)
                return
    except Exception as exc:
        _warn(logger, status, _safe_error(exc))


def _classify(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "success"
    if status_code == 409:
        return "terminal"
    if status_code == 429 or 500 <= status_code <= 599:
        return "retry"
    return "stop"


def _pause(attempt: int, retry_after: str | None, deadline: float) -> bool:
    """Sleep before another attempt. False means the budget cannot fit it."""
    delay = _delay_seconds(attempt, retry_after)
    remaining = deadline - _monotonic()
    if delay >= remaining:
        return False
    if delay > 0:
        _sleep(delay)
    return _monotonic() < deadline


def _delay_seconds(attempt: int, retry_after: str | None) -> float:
    parsed = _retry_after_seconds(retry_after)
    if parsed is not None:
        return parsed
    growth = COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    base = min(growth, _BACKOFF_CAP_SECONDS)
    return base + (base * _JITTER_FRACTION * random.random())


def _retry_after_seconds(raw: str | None) -> float | None:
    """Integer seconds or an HTTP-date, otherwise None.

    None tells the caller to use the default backoff. A date that cannot be
    parsed, including one whose year is out of range, is None. A negative
    delay from a past date is immediate (``0``).
    """
    if raw is None:
        return None
    text = raw.strip()
    if _INTEGER_SECONDS.fullmatch(text):
        return float(int(text))
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        delta = parsed.timestamp() - _wall_time()
    except (OverflowError, OSError, ValueError):
        return None
    if not math.isfinite(delta):
        return None
    return max(0.0, delta)


def _safe_error(exc: BaseException) -> str:
    """Exception type only. The message may contain a URL or request data."""
    return type(exc).__name__


def _warn(logger: logging.Logger, status: str, detail: object) -> None:
    logger.warning("Completion callback failed (%s): %s", status, detail)
