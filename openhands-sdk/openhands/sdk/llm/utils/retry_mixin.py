import time
from collections.abc import Callable, Iterable
from typing import Any, NoReturn, Protocol, cast, runtime_checkable
from weakref import WeakKeyDictionary

from litellm.exceptions import Timeout as LiteLLMTimeout
from tenacity import (
    RetryCallState,
    retry,
    retry_base,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.stop import stop_base

from openhands.sdk.llm.exceptions import LLMNoResponseError
from openhands.sdk.llm.exceptions.classifier import (
    is_quota_exhaustion_error,
    is_transient_http_error,
    is_transient_proxy_auth_error,
)
from openhands.sdk.llm.utils.retry_after import retry_after_seconds
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Helpful alias for listener signature: (attempt_number, max_retries) -> None
RetryListener = Callable[[int, int, BaseException | None], None]

# A call whose attempts keep reaching the per-attempt timeout stops retrying
# once it has used this many timeouts' worth of wall-clock time (#6249).
TIMEOUT_RETRY_BUDGET_MULTIPLIER = 2

# Error classification keys on this phrase to point users at their settings.
TIMEOUT_EXHAUSTED_PHRASE = "did not return a complete response within the LLM timeout"

# Timers can fire up to one clock tick early (about 16 ms on Windows), so an
# attempt this close to the timeout counts as having reached it.
_TIMEOUT_CLOCK_SLACK_SECONDS = 0.05


@runtime_checkable
class _HasStatusCode(Protocol):
    @property
    def status_code(self) -> object: ...


def transient_error_condition(
    exception_types: tuple[type[BaseException], ...],
) -> retry_base:
    """Retry ``exception_types`` and transient HTTP errors, never quota exhaustion."""
    return (
        retry_if_exception_type(exception_types)
        | retry_if_exception(is_transient_http_error)
        # LiteLLM proxy cache-miss 401s recover on retry (OpenHands#18951)
        | retry_if_exception(is_transient_proxy_auth_error)
    ) & retry_if_exception(lambda e: not is_quota_exhaustion_error(e))


class _StopBeforeLongWait(stop_base):
    def __init__(self, max_wait: float):
        self.max_wait = max_wait

    def __call__(self, retry_state: RetryCallState) -> bool:
        error = retry_state.outcome.exception() if retry_state.outcome else None
        return retry_after_seconds(error) > self.max_wait


class _StopAfterTimeoutBudget(stop_base):
    """Stop retrying timeouts once the call has used ``budget`` seconds."""

    def __init__(self, budget: float):
        self.budget = budget

    def __call__(self, retry_state: RetryCallState) -> bool:
        error = retry_state.outcome.exception() if retry_state.outcome else None
        return (
            isinstance(error, LiteLLMTimeout)
            and retry_state.seconds_since_start is not None
            and retry_state.seconds_since_start >= self.budget
        )


class _AttemptClock:
    """Track when each retry call's current attempt started."""

    def __init__(self) -> None:
        self._starts: WeakKeyDictionary[RetryCallState, float] = WeakKeyDictionary()

    def start(self, retry_state: RetryCallState) -> None:
        self._starts[retry_state] = time.monotonic()

    def last_attempt_seconds(self, retry_state: RetryCallState) -> float:
        start = self._starts.get(retry_state, retry_state.start_time)
        end = retry_state.outcome_timestamp or time.monotonic()
        return end - start


def _raise_with_timeout_summary(
    timeout: float, clock: _AttemptClock
) -> Callable[[RetryCallState], NoReturn]:
    """Re-raise the final error.

    A timeout whose attempt ran for the whole per-attempt ``timeout`` gets an
    actionable summary. A quicker timeout (stream idle, connect) is re-raised
    unchanged: its message already names the limit it hit, and raising
    ``timeout`` would not help.
    """

    def callback(retry_state: RetryCallState) -> NoReturn:
        assert retry_state.outcome is not None
        error = retry_state.outcome.exception()
        assert error is not None
        if (
            isinstance(error, LiteLLMTimeout)
            and clock.last_attempt_seconds(retry_state)
            >= timeout - _TIMEOUT_CLOCK_SLACK_SECONDS
        ):
            attempts = retry_state.attempt_number
            elapsed = retry_state.seconds_since_start or 0.0
            error.message = error.message.rstrip(". ") + (
                f". The model {TIMEOUT_EXHAUSTED_PHRASE} of {timeout:g}s "
                f"(gave up after {attempts} attempt{'s' if attempts != 1 else ''}, "
                f"{elapsed:.0f}s in total). Try a faster model, a shorter "
                "prompt (fewer tools or MCP servers), or a higher timeout in "
                "the LLM settings."
            )
        raise error

    return callback


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
                kwargs = getattr(retry_state, "kwargs", None)
                if isinstance(kwargs, dict):
                    current_temp = kwargs.get(
                        "temperature", getattr(self, "temperature", None)
                    )
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
        timeout: float | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Create a LLM retry decorator with customizable parameters.
        This is used for 429 errors, and a few other exceptions in LLM classes.

        ``retry_exceptions`` may be either a tuple of exception types (retried
        as-is) or a tenacity retry predicate (e.g. a combined
        ``retry_if_exception_type(...) & retry_if_not_exception(...)``) for
        finer-grained control over which exceptions are retried.

        ``timeout`` is the per-attempt timeout. When set, timeouts stop being
        retried once the call has spent ``TIMEOUT_RETRY_BUDGET_MULTIPLIER``
        timeouts in total, and a final timeout whose attempt used the whole
        ``timeout`` says so and points at the settings.
        """
        before_sleep = self._build_before_sleep(num_retries, retry_listener)

        retry_condition = (
            retry_if_exception_type(retry_exceptions)
            if isinstance(retry_exceptions, tuple)
            else retry_exceptions
        )

        stop = stop_after_attempt(num_retries) | _StopBeforeLongWait(retry_max_wait)
        if timeout:
            stop = stop | _StopAfterTimeoutBudget(
                TIMEOUT_RETRY_BUDGET_MULTIPLIER * timeout
            )

        def diagnosed_retry(retry_state: RetryCallState) -> bool:
            eligible = retry_condition(retry_state)
            exc = (
                retry_state.outcome.exception()
                if retry_state.outcome is not None
                else None
            )
            if exc is not None:
                status = exc.status_code if isinstance(exc, _HasStatusCode) else None
                logger.warning(
                    "LLM request failed: exception_type=%s.%s status_code=%s "
                    "attempt=%d max_attempts=%d retry_eligible=%s will_retry=%s",
                    type(exc).__module__,
                    type(exc).__qualname__,
                    status if isinstance(status, int) else None,
                    retry_state.attempt_number,
                    num_retries,
                    eligible,
                    eligible and not stop(retry_state),
                )
            return eligible

        exponential_wait = wait_exponential(
            multiplier=retry_multiplier, min=retry_min_wait, max=retry_max_wait
        )

        def wait(retry_state: RetryCallState) -> float:
            error = retry_state.outcome.exception() if retry_state.outcome else None
            return max(exponential_wait(retry_state), retry_after_seconds(error))

        clock = _AttemptClock()
        retry_decorator: Callable[[Callable[..., Any]], Callable[..., Any]] = retry(
            before=clock.start,
            before_sleep=before_sleep,
            stop=stop,
            reraise=True,
            retry=diagnosed_retry,
            wait=wait,
            retry_error_callback=(
                _raise_with_timeout_summary(timeout, clock) if timeout else None
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
        retry_obj = getattr(retry_state, "retry_object", None)
        stop_condition = getattr(retry_obj, "stop", None)
        if stop_condition is not None:
            # stop_any has .stops, single stop does not
            stops: Iterable[Any]
            if hasattr(stop_condition, "stops"):
                stops = stop_condition.stops  # type: ignore[attr-defined]
            else:
                stops = [stop_condition]
            for stop_func in stops:
                if hasattr(stop_func, "max_attempts"):
                    max_attempts = getattr(stop_func, "max_attempts")
                    break

        # Attach dynamic fields for downstream consumers (keep existing behavior)
        setattr(cast(Any, exc), "retry_attempt", retry_state.attempt_number)
        if max_attempts is not None:
            setattr(cast(Any, exc), "max_retries", max_attempts)
