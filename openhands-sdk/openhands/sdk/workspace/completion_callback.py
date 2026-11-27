"""Bounded retry for the automation completion callback.

Both workspace senders call this so status handling cannot drift. The payload,
headers, and the decision to skip a missing URL stay with the caller. This
function never raises: a lost callback must not replace the ``__exit__`` error.

At most four attempts are started. The 30 second monotonic budget only decides
whether another attempt may start and whether a backoff still fits. It does
not cancel a request that has already started. Each attempt uses httpx's sync
timeout, a per-phase idle limit capped at 10 seconds and at the time still
left in the budget.
"""

from __future__ import annotations

import logging
import math
import random
import re
from datetime import UTC
from email.utils import parsedate_to_datetime
from time import monotonic, sleep, time as wall_time
from typing import Any

import httpx


COMPLETION_CALLBACK_MAX_ATTEMPTS = 4
COMPLETION_CALLBACK_BUDGET_SECONDS = 30.0
COMPLETION_CALLBACK_TIMEOUT_SECONDS = 10.0
COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS = 0.25
_BACKOFF_CAP_SECONDS = 2.0
_JITTER_FRACTION = 0.2
# Digits only. float() by itself would accept NaN, Infinity, and "1e2".
_INTEGER_SECONDS = re.compile(r"^[0-9]+$")


def post_completion_callback(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    status: str,
    logger: logging.Logger,
) -> None:
    """POST ``payload`` until it succeeds, is terminal, or the budget runs out.

    Retries transport errors, 429, and 5xx. Any 2xx is success. 409 stops
    without a failure warning. Other 4xx responses are not retried. A log
    names an exception type or a status code, not the request or its text.
    """
    deadline = monotonic() + COMPLETION_CALLBACK_BUDGET_SECONDS
    try:
        with httpx.Client() as client:
            for attempt in range(1, COMPLETION_CALLBACK_MAX_ATTEMPTS + 1):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    _warn(logger, status, "retry budget exhausted")
                    return
                timeout = min(COMPLETION_CALLBACK_TIMEOUT_SECONDS, remaining)
                try:
                    response = client.post(
                        url, json=payload, headers=headers, timeout=timeout
                    )
                except httpx.TransportError as exc:
                    if _again(attempt, None, deadline):
                        continue
                    _warn(logger, status, type(exc).__name__)
                    return

                code = response.status_code
                if 200 <= code < 300:
                    logger.info("Completion callback sent (%s): %s", status, code)
                    return
                if code == 409:
                    logger.info(
                        "Completion callback already terminal (%s): %s", status, code
                    )
                    return
                if code == 429 or 500 <= code <= 599:
                    if _again(attempt, response.headers.get("Retry-After"), deadline):
                        continue
                _warn(logger, status, code)
                return
    except Exception as exc:
        _warn(logger, status, type(exc).__name__)


def _again(attempt: int, retry_after: str | None, deadline: float) -> bool:
    """Sleep if another attempt still fits. False means stop."""
    if attempt >= COMPLETION_CALLBACK_MAX_ATTEMPTS:
        return False
    delay = _delay(attempt, retry_after)
    if delay >= deadline - monotonic():
        return False
    if delay > 0:
        sleep(delay)
    return monotonic() < deadline


def _delay(attempt: int, retry_after: str | None) -> float:
    parsed = _retry_after(retry_after)
    if parsed is not None:
        return parsed
    growth = COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    base = min(growth, _BACKOFF_CAP_SECONDS)
    return base + (base * _JITTER_FRACTION * random.random())


def _retry_after(raw: str | None) -> float | None:
    """Integer seconds or an HTTP-date. None means use the normal backoff.

    A digit string that does not fit in a float becomes infinite, so the
    budget check stops with a status warning instead of raising. An
    unparseable or out-of-range date is ignored.
    """
    if raw is None:
        return None
    text = raw.strip()
    if _INTEGER_SECONDS.fullmatch(text):
        try:
            return float(text)
        except (ValueError, OverflowError):
            return math.inf
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        delta = parsed.timestamp() - wall_time()
    except (OverflowError, OSError, ValueError):
        return None
    if not math.isfinite(delta):
        return None
    return max(0.0, delta)


def _warn(logger: logging.Logger, status: str, detail: object) -> None:
    logger.warning("Completion callback failed (%s): %s", status, detail)
