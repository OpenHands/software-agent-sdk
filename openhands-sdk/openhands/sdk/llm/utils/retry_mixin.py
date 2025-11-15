from collections.abc import Callable, Iterable
from typing import Any, cast

from litellm.exceptions import InternalServerError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from openhands.sdk.llm.exceptions import LLMNoResponseError
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Helpful alias for listener signature: (attempt_number, max_retries) -> None
RetryListener = Callable[[int, int], None]


def _looks_like_choices_none_error(msg: str) -> bool:
    m = msg.lower()
    if "choices" not in m:
        return False
    # Heuristics matching LiteLLM response conversion assertions/validation
    return any(k in m for k in ("none", "assert", "invalid"))


class RetryMixin:
    """Mixin class for retry logic."""

    def retry_decorator(
        self,
        num_retries: int = 5,
        retry_exceptions: tuple[type[BaseException], ...] = (LLMNoResponseError,),
        retry_min_wait: int = 8,
        retry_max_wait: int = 64,
        retry_multiplier: float = 2.0,
        retry_listener: RetryListener | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Create a LLM retry decorator with customizable parameters.
        This is used for 429 errors, and a few other exceptions in LLM classes.
        """

        def before_sleep(retry_state: RetryCallState) -> None:
            # Log first (also validates outcome as part of logging)
            self.log_retry_attempt(retry_state)

            if retry_listener is not None:
                retry_listener(retry_state.attempt_number, num_retries)

            # If there is no outcome or no exception, nothing to tweak.
            if retry_state.outcome is None:
                return
            exc = retry_state.outcome.exception()
            if exc is None:
                return

            # Adjust temperature for LLMNoResponseError or certain InternalServerError
            should_bump = isinstance(exc, LLMNoResponseError) or (
                isinstance(exc, InternalServerError)
                and _looks_like_choices_none_error(str(exc))
            )
            if should_bump:
                kwargs = getattr(retry_state, "kwargs", None)
                if isinstance(kwargs, dict):
                    current_temp = kwargs.get("temperature", 0)
                    if current_temp == 0:
                        kwargs["temperature"] = 1.0
                        logger.warning(
                            "LLMNoResponse-like error with temperature=0, "
                            "setting temperature to 1.0 for next attempt."
                        )
                    else:
                        logger.warning(
                            "LLMNoResponse-like error with temperature="
                            f"{current_temp}, keeping original temperature"
                        )

        tenacity_decorator: Callable[[Callable[..., Any]], Callable[..., Any]] = retry(
            before_sleep=before_sleep,
            stop=stop_after_attempt(num_retries),
            reraise=True,
            retry=retry_if_exception_type(retry_exceptions),
            wait=wait_exponential(
                multiplier=retry_multiplier,
                min=retry_min_wait,
                max=retry_max_wait,
            ),
        )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                try:
                    return func(*args, **kwargs)
                except InternalServerError as e:
                    if _looks_like_choices_none_error(str(e)):
                        raise LLMNoResponseError(
                            f"Provider returned malformed response: {e}"
                        ) from e
                    raise

            return tenacity_decorator(wrapped)

        return decorator

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

        logger.error(
            "%s. Attempt #%d | You can customize retry values in the configuration.",
            exc,
            retry_state.attempt_number,
        )
