import asyncio
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from litellm.exceptions import RateLimitError

from openhands.sdk.llm.utils.retry_after import retry_after_seconds
from openhands.sdk.llm.utils.retry_mixin import RetryMixin


def rate_limit(headers=None, source="response"):
    response = httpx.Response(
        429, request=httpx.Request("POST", "https://test.invalid")
    )
    error = RateLimitError("temporary rate limit", "openai", "test", response=response)
    if source == "response":
        error.response.headers.update(headers or {})
    elif source == "headers":
        error.headers = headers
    else:
        cast(Any, error).litellm_response_headers = headers
    return error


@pytest.mark.parametrize("source", ["response", "headers", "litellm"])
@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("Retry-After", "12", 12),
        ("retry-after", "0.25", 0.25),
        ("RETRY-AFTER-MS", "1250", 1.25),
        ("LLM_PROVIDER-Retry-After", "12", 12),
        ("llm_provider-retry-after-ms", "1250", 1.25),
        ("retry-after", "0", 0),
        ("retry-after", "-1", 0),
        ("retry-after", "NaN", 0),
        ("retry-after", "inf", 0),
        ("retry-after", "1e999", 0),
        ("retry-after", "invalid", 0),
        ("retry-after-ms", "invalid", 0),
        ("retry-after", "", 0),
        ("other-header", "12", 0),
    ],
)
def test_retry_hints(source, name, value, expected):
    assert retry_after_seconds(rate_limit({name: value}, source)) == expected


@pytest.mark.parametrize("offset", [12, -12])
def test_http_date(offset):
    now = 1800000000
    date = format_datetime(datetime.fromtimestamp(now + offset, UTC), usegmt=True)
    with patch("openhands.sdk.llm.utils.retry_after.time.time", return_value=now):
        assert retry_after_seconds(rate_limit({"Retry-After": date})) == max(offset, 0)


def test_longest_valid_hint_wins_across_sources():
    error = rate_limit({"retry-after": "9"})
    error.headers = {"Retry-After": "invalid", "retry-after-ms": "12000"}
    cast(Any, error).litellm_response_headers = {"llm_provider-retry-after": "15"}
    assert retry_after_seconds(error) == 15


def test_errors_without_headers():
    assert retry_after_seconds(None) == 0
    assert retry_after_seconds(ValueError("no headers")) == 0
    assert retry_after_seconds(rate_limit()) == 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(("hint", "expected_wait"), [("12", 12), ("2", 8), ("bad", 8)])
def test_retry_wait_and_attempt_count(asynchronous, hint, expected_wait):
    calls = []
    waits = []
    events = []
    error = rate_limit({"retry-after": hint})
    decorator = RetryMixin().retry_decorator(
        num_retries=3,
        retry_exceptions=(RateLimitError,),
        retry_listener=lambda *args: events.append(args),
    )

    def attempt():
        calls.append(1)
        if len(calls) < 3:
            raise error
        return "ok"

    async def async_attempt():
        return attempt()

    async def async_sleep(seconds):
        waits.append(seconds)

    if asynchronous:
        wrapped = decorator(async_attempt)
        cast(Any, wrapped).retry.sleep = async_sleep
        assert asyncio.run(wrapped()) == "ok"
    else:
        wrapped = decorator(attempt)
        cast(Any, wrapped).retry.sleep = waits.append
        assert wrapped() == "ok"
    assert len(calls) == 3
    assert waits == [expected_wait, expected_wait]
    assert [(event[0], event[1]) for event in events] == [(1, 3), (2, 3)]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(("hint", "max_wait"), [("65", 64), ("1e300", 64), ("1", 0)])
def test_over_limit_hint_surfaces_original_error_without_sleep(
    asynchronous, hint, max_wait
):
    error = rate_limit({"retry-after": hint})
    calls = []
    waits = []
    decorator = RetryMixin().retry_decorator(
        num_retries=3,
        retry_exceptions=(RateLimitError,),
        retry_max_wait=max_wait,
        retry_min_wait=0,
    )

    def attempt():
        calls.append(1)
        raise error

    async def async_attempt():
        attempt()

    async def async_sleep(seconds):
        waits.append(seconds)

    wrapped = decorator(async_attempt if asynchronous else attempt)
    cast(Any, wrapped).retry.sleep = async_sleep if asynchronous else waits.append
    with pytest.raises(RateLimitError) as caught:
        asyncio.run(wrapped()) if asynchronous else wrapped()
    assert caught.value is error
    assert len(calls) == 1
    assert not waits


def test_exhausted_attempts_do_not_add_a_sleep():
    error = rate_limit({"retry-after": "12"})
    calls = []
    waits = []

    @RetryMixin().retry_decorator(num_retries=2, retry_exceptions=(RateLimitError,))
    def attempt():
        calls.append(1)
        raise error

    cast(Any, attempt).retry.sleep = waits.append
    with pytest.raises(RateLimitError):
        attempt()
    assert len(calls) == 2
    assert waits == [12]


@pytest.mark.asyncio
async def test_cancellation_during_provider_wait_stops_retry():
    calls = []
    sleeping = asyncio.Event()

    @RetryMixin().retry_decorator(retry_exceptions=(RateLimitError,))
    async def attempt():
        calls.append(1)
        raise rate_limit({"retry-after": "30"})

    async def sleep(seconds):
        assert seconds == 30
        sleeping.set()
        await asyncio.sleep(seconds)

    cast(Any, attempt).retry.sleep = sleep
    task = asyncio.create_task(attempt())
    await asyncio.wait_for(sleeping.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1


def test_missing_hint_preserves_existing_backoff_when_min_exceeds_max():
    calls = []
    waits = []

    @RetryMixin().retry_decorator(
        num_retries=2,
        retry_exceptions=(RateLimitError,),
        retry_min_wait=8,
        retry_max_wait=0,
    )
    def attempt():
        calls.append(1)
        raise rate_limit()

    cast(Any, attempt).retry.sleep = waits.append
    with pytest.raises(RateLimitError):
        attempt()
    assert len(calls) == 2
    assert waits == [8]
