from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from tenacity import (
    RetryCallState,
    retry,
    retry_base,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from openhands.sdk.llm.exceptions import (
    LLMNoResponseError,
    SupportsMaxRetries,
    SupportsRetryMetadata,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Helpful alias for listener signature: (attempt_number, max_retries) -> None
RetryListener = Callable[[int, int, BaseException | None], None]


@runtime_checkable
class SupportsTemperature(Protocol):
    """Protocol for objects exposing a configured temperature."""

    temperature: float | None


@runtime_checkable
class SupportsMaxAttemptNumber(Protocol):
    """Protocol for tenacity stop predicates declaring max_attempt_number."""

    max_attempt_number: int


@runtime_checkable
class SupportsMaxAttempts(Protocol):
    """Protocol for stop predicates declaring max_attempts."""

    max_attempts: int


@runtime_checkable
class SupportsCompoundStops(Protocol):
    """Protocol for compound tenacity stop conditions (e.g. stop_any, stop_all)."""

    stops: Iterable[Any]


@runtime_checkable
class SupportsStopCondition(Protocol):
    """Protocol for tenacity retry objects declaring a stop condition."""

    stop: Any


def extract_max_retries(stop_condition: Any) -> int | None:
    """Extract maximum attempt limit from a tenacity stop condition, if bounded.

    Normalizes access across single stop conditions (e.g. ``stop_after_attempt``),
    compound stop conditions (e.g. ``stop_any``, ``stop_all``), and custom stop
    predicates. Returns ``None`` for unbounded retries (e.g. ``stop_never``).
    """
    if stop_condition is None:
        return None

    if isinstance(stop_condition, SupportsCompoundStops):
        for stop_func in stop_condition.stops:
            max_val = extract_max_retries(stop_func)
            if max_val is not None:
                return max_val
        return None

    if isinstance(stop_condition, SupportsMaxAttemptNumber):
        return stop_condition.max_attempt_number

    if isinstance(stop_condition, SupportsMaxAttempts):
        return stop_condition.max_attempts

    return None


class RetryMixin:
    """Mixin class for retry logic."""

    def _build_before_sleep(
        self,
        num_retries: int,
        retry_listener: RetryListener | None,
    ) -> Callable[[RetryCallState], None]:
        """Build a ``before_sleep`` callback shared by sync and async decorators."""

        def before_sleep(retry_state: RetryCallState) -> None:
            self.log_retry_attempt(retry_state)

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

        retry_condition = (
            retry_if_exception_type(retry_exceptions)
            if isinstance(retry_exceptions, tuple)
            else retry_exceptions
        )

        retry_decorator: Callable[[Callable[..., Any]], Callable[..., Any]] = retry(
            before_sleep=before_sleep,
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

    def log_retry_attempt(self, retry_state: RetryCallState) -> None:
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

        # Try to get max attempts from the stop condition if present
        max_attempts: int | None = None
        retry_obj = retry_state.retry_object
        if isinstance(retry_obj, SupportsStopCondition):
            max_attempts = extract_max_retries(retry_obj.stop)

        # Attach typed fields for downstream consumers and listeners
        if isinstance(exc, SupportsRetryMetadata):
            exc.retry_attempt = retry_state.attempt_number
            if max_attempts is not None:
                exc.max_retries = max_attempts
        elif isinstance(exc, SupportsMaxRetries):
            # Third-party transport exceptions (e.g. litellm) declare max_retries
            # but not retry_attempt; attach retry_attempt defensively so listeners
            # and mapping receive it.
            try:
                exc.retry_attempt = retry_state.attempt_number  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
            if max_attempts is not None:
                exc.max_retries = max_attempts

        logger.error(
            "%s. Attempt #%d | You can customize retry values in the configuration.",
            exc,
            retry_state.attempt_number,
        )
