import logging

import pytest
from litellm.exceptions import APIError
from tenacity import retry_if_exception_type

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
