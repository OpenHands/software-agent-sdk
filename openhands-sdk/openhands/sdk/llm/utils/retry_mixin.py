from collections.abc import Callable
from typing import Any, Protocol, cast, runtime_checkable

from tenacity import (
    RetryCallState,
    retry,
    retry_base,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from openhands.sdk.llm.exceptions import LLMError, LLMNoResponseError
from openhands.sdk.llm.exceptions.mapping import attach_exhausted_retry_metadata
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Helpful alias for listener signature: (attempt_number, max_retries) -> None
RetryListener = Callable[[int, int, BaseException | None], None]


@runtime_checkable
class SupportsTemperature(Protocol):
    """Protocol for objects exposing a configured temperature."""

    temperature: float | None


class RetryMixin:
    """Mixin class for retry logic."""

    def _build_before_sleep(
        self,
        num_retries: int,
        retry_listener: RetryListener | None,
    ) -> Callable[[RetryCallState], None]:
        """Build a ``before_sleep`` callback shared by sync and async decorators."""

        def before_sleep(retry_state: RetryCallState) -> None:
            self.log_retry_attempt(retry_state, num_retries)

            if retry_listener is not None:
                exc = (
                    retry_state.outcome.exception()
                    if retry_state.outcome is not None
                    else None
                )
                retry_listener(retry_state.attempt_number, num_retries, exc)

            if retry_state.outcome is None:
                return
            exc = retry_state.outcome.exception()
            if exc is None:
                return

            # Only adjust temperature for LLMNoResponseError
            if isinstance(exc, LLMNoResponseError):
                kwargs = retry_state.kwargs
                if isinstance(kwargs, dict):
                    configured_temp: float | None = (
                        self.temperature
                        if isinstance(self, SupportsTemperature)
                        else None
                    )
                    current_temp = kwargs.get("temperature", configured_temp)
                    if current_temp is None:
                        logger.warning(
                            "LLMNoResponseError with no configured temperature, "
                            "leaving temperature unset for next attempt."
                        )
                        return
                    if current_temp == 0:
                        kwargs["temperature"] = 1.0
                        logger.warning(
                            "LLMNoResponseError with temperature=0, "
                            "setting temperature to 1.0 for next attempt."
                        )
                    else:
                        logger.warning(
                            f"LLMNoResponseError with temperature={current_temp}, "
                            "keeping original temperature"
                        )

        return before_sleep

    def retry_decorator(
        self,
        num_retries: int = 5,
        retry_exceptions: tuple[type[BaseException], ...] | retry_base = (
            LLMNoResponseError,
        ),
        retry_min_wait: int = 8,
        retry_max_wait: int = 64,
        retry_multiplier: float = 2.0,
        retry_listener: RetryListener | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Create a LLM retry decorator with customizable parameters.
        This is used for 429 errors, and a few other exceptions in LLM classes.

        ``retry_exceptions`` may be either a tuple of exception types (retried
        as-is) or a tenacity retry predicate (e.g. a combined
        ``retry_if_exception_type(...) & retry_if_not_exception(...)``) for
        finer-grained control over which exceptions are retried.
        """
        before_sleep = self._build_before_sleep(num_retries, retry_listener)

        def on_exhausted(retry_state: RetryCallState) -> Any:
            # Tenacity skips reraise when this callback is set, and before_sleep
            # does not run for the final attempt. Stamp that attempt, then
            # re-raise the original exception.
            outcome = retry_state.outcome
            exc = outcome.exception() if outcome is not None else None
            if exc is not None:
                attach_exhausted_retry_metadata(
                    exc, retry_state.attempt_number, num_retries
                )
            if outcome is not None:
                return outcome.result()
            return None

        retry_condition = (
            retry_if_exception_type(retry_exceptions)
            if isinstance(retry_exceptions, tuple)
            else retry_exceptions
        )

        retry_decorator: Callable[[Callable[..., Any]], Callable[..., Any]] = retry(
            before_sleep=before_sleep,
            retry_error_callback=on_exhausted,
            stop=stop_after_attempt(num_retries),
            reraise=True,
            retry=retry_condition,
            wait=wait_exponential(
                multiplier=retry_multiplier,
                min=retry_min_wait,
                max=retry_max_wait,
            ),
        )
        return retry_decorator

    def log_retry_attempt(
        self, retry_state: RetryCallState, num_retries: int | None = None
    ) -> None:
        """Log retry attempts."""

        if retry_state.outcome is None:
            logger.error(
                "retry_state.outcome is None. "
                "This should not happen, please check the retry logic."
            )
            return

        exc = retry_state.outcome.exception()
        if exc is None:
            logger.error("retry_state.outcome.exception() returned None.")
            return

        attempt = retry_state.attempt_number
        if isinstance(exc, LLMError):
            exc.retry_attempt = attempt
            exc.max_retries = num_retries
        else:
            try:
                cast(Any, exc).retry_attempt = attempt
            except (AttributeError, TypeError):
                return

        logger.error(
            "%s. Attempt #%d | You can customize retry values in the configuration.",
            exc,
            retry_state.attempt_number,
        )
