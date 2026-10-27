import asyncio
from unittest.mock import MagicMock

import pytest
from litellm.exceptions import APIConnectionError, RateLimitError
from tenacity import Future, RetryCallState

from openhands.sdk.llm.exceptions import (
    LLMNoResponseError,
    LLMRateLimitError,
    map_provider_exception,
)
from openhands.sdk.llm.utils.retry_mixin import RetryMixin


def _state(exc: BaseException, attempt: int) -> RetryCallState:
    future = Future(attempt_number=attempt)
    future.set_exception(exc)
    state = RetryCallState(MagicMock(), fn=lambda: None, args=(), kwargs={})
    state.attempt_number = attempt
    state.outcome = future
    return state


def test_log_retry_attempt_bounded():
    """log_retry_attempt records the decorator's own attempt limit."""
    exc = LLMNoResponseError("timeout")
    RetryMixin().log_retry_attempt(_state(exc, 2), num_retries=3)
    assert exc.retry_attempt == 2
    assert exc.max_retries == 3


def test_log_retry_attempt_without_limit():
    """log_retry_attempt leaves max_retries unset when no limit is passed."""
    exc = LLMNoResponseError("unbounded error")
    RetryMixin().log_retry_attempt(_state(exc, 5))
    assert exc.retry_attempt == 5
    assert exc.max_retries is None


def test_log_retry_attempt_does_not_rewrite_litellm_max_retries():
    """retry_attempt is attached without changing the text LiteLLM prints."""
    exc = APIConnectionError("connection failed", "test_provider", "test_model")
    RetryMixin().log_retry_attempt(_state(exc, 2), num_retries=3)
    assert getattr(exc, "retry_attempt", None) == 2
    assert exc.max_retries is None
    assert "LiteLLM Max Retries" not in str(exc)


def test_log_retry_attempt_sets_retry_attempt_on_custom_errors():
    """Listeners can read retry_attempt on errors that have no max_retries."""
    exc = RuntimeError("generic error")
    RetryMixin().log_retry_attempt(_state(exc, 1), num_retries=4)
    assert getattr(exc, "retry_attempt", None) == 1


def test_custom_retry_listener_sees_retry_attempt():
    class MyError(Exception):
        pass

    seen: list[int] = []

    def listener(attempt: int, limit: int, exc: BaseException | None) -> None:
        assert exc is not None
        seen.append(getattr(exc, "retry_attempt"))

    mixin = RetryMixin()

    @mixin.retry_decorator(
        num_retries=3,
        retry_exceptions=(MyError,),
        retry_min_wait=0,
        retry_max_wait=0,
        retry_listener=listener,
    )
    def flaky() -> None:
        raise MyError("fail")

    with pytest.raises(MyError):
        flaky()

    assert seen == [1, 2]


def _exhausting(mixin: RetryMixin, exceptions: tuple[type[BaseException], ...]):
    return mixin.retry_decorator(
        num_retries=3,
        retry_exceptions=exceptions,
        retry_min_wait=0,
        retry_max_wait=0,
    )


def test_exhausted_retries_stamp_the_final_new_exception():
    """Each attempt raises a new error, so only the exhaustion path can stamp it."""
    mixin = RetryMixin()
    raised: list[LLMNoResponseError] = []

    @_exhausting(mixin, (LLMNoResponseError,))
    def flaky() -> None:
        exc = LLMNoResponseError("no response")
        raised.append(exc)
        raise exc

    with pytest.raises(LLMNoResponseError) as err:
        flaky()

    assert len(raised) == 3
    assert err.value is raised[-1]
    assert err.value.retry_attempt == 3
    assert err.value.max_retries == 3
    assert raised[0].retry_attempt == 1
    assert raised[1].retry_attempt == 2


def test_exhausted_retries_stamp_a_reused_exception_with_the_final_attempt():
    """Reusing one exception must not keep the previous attempt's stamp."""
    mixin = RetryMixin()
    exc = LLMNoResponseError("no response")

    @_exhausting(mixin, (LLMNoResponseError,))
    def flaky() -> None:
        raise exc

    with pytest.raises(LLMNoResponseError):
        flaky()

    assert exc.retry_attempt == 3
    assert exc.max_retries == 3


def test_exhausted_provider_exception_keeps_metadata_after_mapping():
    """A fresh LiteLLM error per attempt still reaches the mapped SDK error."""
    mixin = RetryMixin()

    @_exhausting(mixin, (RateLimitError,))
    def flaky() -> None:
        raise RateLimitError("rate limit", "gpt-4o", "openai")

    with pytest.raises(RateLimitError) as err:
        flaky()

    assert "LiteLLM Max Retries" not in str(err.value)
    mapped = map_provider_exception(err.value)
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_attempt == 3
    assert mapped.max_retries == 3
    assert "LiteLLM Max Retries" not in str(mapped)


def test_exhausted_async_retries_stamp_the_final_exception():
    mixin = RetryMixin()

    @_exhausting(mixin, (LLMNoResponseError,))
    async def flaky() -> None:
        raise LLMNoResponseError("no response")

    with pytest.raises(LLMNoResponseError) as err:
        asyncio.run(flaky())

    assert err.value.retry_attempt == 3
    assert err.value.max_retries == 3
