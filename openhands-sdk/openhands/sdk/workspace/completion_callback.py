"""Bounded retry for the automation completion callback.

Both workspace senders call this so status handling cannot drift. The payload,
headers, and the decision to skip a missing URL stay with the caller. This
function never raises: a lost callback must not replace the ``__exit__`` error.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


# Four attempts and about 30 seconds, including request time and waits.
# A valid Retry-After is used instead of the backoff when it still fits.
COMPLETION_CALLBACK_MAX_ATTEMPTS = 4
COMPLETION_CALLBACK_BUDGET_SECONDS = 30.0
COMPLETION_CALLBACK_TIMEOUT_SECONDS = 10.0
COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS = 0.25
_BACKOFF_CAP_SECONDS = 2.0
_JITTER_FRACTION = 0.2


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
    not retried. Request headers are not written to the log.
    """
    deadline = time.monotonic() + COMPLETION_CALLBACK_BUDGET_SECONDS
    try:
        with httpx.Client() as client:
            for attempt in range(1, COMPLETION_CALLBACK_MAX_ATTEMPTS + 1):
                remaining = deadline - time.monotonic()
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
                if kind == "retry" and attempt < COMPLETION_CALLBACK_MAX_ATTEMPTS:
                    if _pause(attempt, response, deadline):
                        continue
                _warn(logger, status, response.status_code)
                return
    except Exception as exc:
        _warn(logger, status, _safe_error(exc))


def _classify(status_code: object) -> str:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        return "stop"
    if 200 <= status_code < 300:
        return "success"
    if status_code == 409:
        return "terminal"
    if status_code == 429 or 500 <= status_code <= 599:
        return "retry"
    return "stop"


def _pause(attempt: int, response: httpx.Response | None, deadline: float) -> bool:
    """Sleep before another attempt. False means the budget cannot fit it."""
    delay = _delay_seconds(attempt, response)
    remaining = deadline - time.monotonic()
    if delay >= remaining:
        return False
    if delay > 0:
        time.sleep(delay)
    return time.monotonic() < deadline


def _delay_seconds(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return retry_after
    growth = COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    base = min(growth, _BACKOFF_CAP_SECONDS)
    return base + (base * _JITTER_FRACTION * random.random())


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds from a usable Retry-After, or None when it should be ignored."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    else:
        if seconds < 0:
            return None
        return seconds
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, parsed.timestamp() - time.time())


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    if "Authorization" in text or "Bearer " in text:
        return type(exc).__name__
    return f"{type(exc).__name__}: {text}"


def _warn(logger: logging.Logger, status: str, detail: object) -> None:
    logger.warning("Completion callback failed (%s): %s", status, detail)
