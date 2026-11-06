"""Read provider retry delays from transport and LiteLLM errors."""

import math
import time
from collections.abc import Mapping
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class _HasResponse(Protocol):
    response: httpx.Response


@runtime_checkable
class _HasHeaders(Protocol):
    headers: Mapping[str, str] | None


@runtime_checkable
class _HasLiteLLMHeaders(Protocol):
    litellm_response_headers: Mapping[str, str] | None


def _parse_delay(value: str, milliseconds: bool) -> float | None:
    try:
        delay = float(value) / (1000 if milliseconds else 1)
    except ValueError:
        if milliseconds:
            return None
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            delay = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return delay if math.isfinite(delay) and delay >= 0 else None


def retry_after_seconds(error: BaseException | None) -> float:
    """Return the longest valid retry hint, or zero when none is available."""
    sources: list[Mapping[str, str]] = []
    if isinstance(error, _HasResponse) and isinstance(error.response, httpx.Response):
        sources.append(error.response.headers)
    if isinstance(error, _HasHeaders) and isinstance(error.headers, Mapping):
        sources.append(error.headers)
    if isinstance(error, _HasLiteLLMHeaders) and isinstance(
        error.litellm_response_headers, Mapping
    ):
        sources.append(error.litellm_response_headers)

    longest = 0.0
    for headers in sources:
        for name, value in headers.items():
            name = name.lower().removeprefix("llm_provider-")
            if name not in ("retry-after", "retry-after-ms"):
                continue
            if not isinstance(value, str):
                continue
            delay = _parse_delay(value, milliseconds=name == "retry-after-ms")
            if delay is not None:
                longest = max(longest, delay)
    return longest
