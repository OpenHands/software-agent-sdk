import logging
from typing import ClassVar

import pytest
from litellm.exceptions import APIError
from tenacity import retry_if_exception, retry_if_exception_type

from openhands.sdk.llm.exceptions.classifier import is_transient_http_error
from openhands.sdk.llm.utils.retry_mixin import RetryMixin


@pytest.mark.parametrize("attempts,eligible", [(3, True), (1, True), (3, False)])
def test_failure_logging_explains_retry_decision(caplog, attempts, eligible):
    calls = 0
    error = APIError(
        status_code=502,
        message="secret-response-body",
        llm_provider="openrouter",
        model="test",
    )
    policy = RetryMixin().retry_decorator(
        num_retries=attempts,
        retry_exceptions=retry_if_exception_type(
            APIError if eligible else TimeoutError
        ),
        retry_min_wait=0,
        retry_max_wait=0,
    )

    @policy
    def request():
        nonlocal calls
        calls += 1
        raise error

    with caplog.at_level(logging.WARNING), pytest.raises(APIError):
        request()
    assert calls == (attempts if eligible else 1)
    messages = [
        r.getMessage()
        for r in caplog.records
        if "LLM request failed:" in r.getMessage()
    ]
    assert len(messages) == calls
    assert "litellm.exceptions.APIError" in messages[-1]
    assert "status_code=502" in messages[-1]
    assert "will_retry=False" in messages[-1]
    if calls > 1:
        assert "will_retry=True" in messages[0]
    assert "secret-response-body" not in caplog.text


@pytest.mark.parametrize("status", [None, 429, "private-status"])
def test_failure_logging_handles_optional_status_safely(caplog, status):
    class ProviderError(Exception):
        status_code = status

    error = (
        RuntimeError("private-body")
        if status is None
        else ProviderError("private-body")
    )

    @RetryMixin().retry_decorator(num_retries=1, retry_exceptions=(Exception,))
    def request():
        raise error

    with caplog.at_level(logging.WARNING), pytest.raises(type(error)):
        request()

    expected_status = status if isinstance(status, int) else None
    assert f"status_code={expected_status}" in caplog.text
    assert "will_retry=False" in caplog.text
    assert "private-status" not in caplog.text
    assert "private-body" not in caplog.text


@pytest.mark.asyncio
async def test_async_retry_logs_failure_and_preserves_success(caplog):
    calls = 0

    @RetryMixin().retry_decorator(
        num_retries=2,
        retry_exceptions=(APIError,),
        retry_min_wait=0,
        retry_max_wait=0,
    )
    async def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise APIError(
                status_code=503,
                message="private-body",
                llm_provider="openrouter",
                model="test",
            )
        return "ok"

    with caplog.at_level(logging.WARNING):
        assert await request() == "ok"
    assert calls == 2
    assert "will_retry=True" in caplog.text
    assert "private-body" not in caplog.text


@pytest.mark.parametrize("status", [200, 422])
@pytest.mark.parametrize("body", [" \n\n", "<html>upstream error</html>"])
def test_malformed_provider_response_uses_existing_retry_budget(status, body):
    error = APIError(
        status_code=status,
        message="Unable to get json response - Expecting value: line 1 column 1 "
        "(char 0), Original Response: " + body,
        llm_provider="openrouter",
        model="test",
    )
    calls = 0

    @RetryMixin().retry_decorator(
        num_retries=3,
        retry_exceptions=retry_if_exception(is_transient_http_error),
        retry_min_wait=0,
        retry_max_wait=0,
    )
    def request():
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(APIError):
        request()
    assert calls == (3 if status == 200 else 1)


@pytest.mark.parametrize(
    "status,message",
    [
        (200, "Invalid JSON in model output"),
        (200, "Unable to get json response: invalid request"),
        (400, "Unable to get json response - Expecting value, Original Response: "),
        (401, "Unable to get json response - Expecting value, Original Response: "),
        (403, "Unable to get json response - Expecting value, Original Response: "),
    ],
)
def test_unrelated_api_errors_are_not_transient(status, message):
    error = APIError(
        status_code=status, message=message, llm_provider="openrouter", model="test"
    )
    assert not is_transient_http_error(error)


@pytest.mark.asyncio
async def test_malformed_provider_response_recovers_on_async_retry():
    calls = 0

    @RetryMixin().retry_decorator(
        num_retries=3,
        retry_exceptions=retry_if_exception(is_transient_http_error),
        retry_min_wait=0,
        retry_max_wait=0,
    )
    async def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise APIError(
                status_code=200,
                message=(
                    "Unable to get json response - Expecting value, Original Response: "
                ),
                llm_provider="openrouter",
                model="test",
            )
        return "ok"

    assert await request() == "ok"
    assert calls == 2


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_long_retry_after_logs_no_retry(caplog, asynchronous):
    class RateLimitedError(Exception):
        status_code = 429
        headers: ClassVar[dict[str, str]] = {"retry-after": "300"}

    calls = 0
    policy = RetryMixin().retry_decorator(
        num_retries=3,
        retry_exceptions=(RateLimitedError,),
        retry_min_wait=0,
        retry_max_wait=64,
    )

    def fail():
        nonlocal calls
        calls += 1
        raise RateLimitedError("private-response-body")

    @policy
    def request():
        fail()

    @policy
    async def async_request():
        fail()

    with caplog.at_level(logging.WARNING), pytest.raises(RateLimitedError):
        if asynchronous:
            await async_request()
        else:
            request()

    assert calls == 1
    assert "retry_eligible=True will_retry=False" in caplog.text
    assert "private-response-body" not in caplog.text
