import os
from collections.abc import Callable
from contextvars import Context, Token
from typing import (
    Any,
    Literal,
)

import litellm
from dotenv import dotenv_values
from lmnr import Laminar, LaminarLiteLLMCallback, observe as laminar_observe
from opentelemetry import trace

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def maybe_init_laminar():
    """Initialize Laminar if the environment variables are set.

    Example configuration:
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4317/v1/traces

    # comma separated, key=value url-encoded pairs
    OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20<KEY>,X-Key=<CUSTOM_VALUE>"

    # grpc is assumed if not specified
    OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf # or grpc/protobuf
    # or
    OTEL_EXPORTER=otlp_http # or otlp_grpc
    """
    if should_enable_observability():
        Laminar.initialize()
        litellm.callbacks.append(LaminarLiteLLMCallback())
    else:
        logger.debug(
            "Observability/OTEL environment variables are not set. "
            "Skipping Laminar initialization."
        )


def observe[**P, R](
    *,
    name: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    ignore_input: bool = False,
    ignore_output: bool = False,
    span_type: Literal["DEFAULT", "LLM", "TOOL"] = "DEFAULT",
    ignore_inputs: list[str] | None = None,
    input_formatter: Callable[P, str] | None = None,
    output_formatter: Callable[[R], str] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    preserve_global_context: bool = False,
    **kwargs: dict[str, Any],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        return laminar_observe(
            name=name,
            session_id=session_id,
            user_id=user_id,
            ignore_input=ignore_input,
            ignore_output=ignore_output,
            span_type=span_type,
            ignore_inputs=ignore_inputs,
            input_formatter=input_formatter,
            output_formatter=output_formatter,
            metadata=metadata,
            tags=tags,
            preserve_global_context=preserve_global_context,
            **kwargs,
        )(func)

    return decorator


def should_enable_observability():
    dotenv_vals = dotenv_values()
    keys = [
        "LMNR_PROJECT_API_KEY",
        "OTEL_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ]
    if any(dotenv_vals.get(key) for key in keys) or any(os.getenv(key) for key in keys):
        return True
    if Laminar.is_initialized():
        return True
    return False


class SpanManager:
    """Manages a stack of active spans and their associated tokens."""

    def __init__(self):
        self._stack: list[tuple[trace.Span, Token[Context] | None]] = []

    def start_active_span(self, name: str, session_id: str | None = None) -> None:
        """Start a new active span and push it to the stack."""
        span, token = Laminar.start_active_span(name)
        if session_id:
            Laminar.set_trace_session_id(session_id)
        self._stack.append((span, token))

    def end_active_span(self) -> None:
        """End the most recent active span by popping it from the stack."""
        if not self._stack:
            logger.warning("Attempted to end active span, but stack is empty")
            return

        span, token = self._stack.pop()
        if token is not None:
            Laminar.end_active_span(span, token)
        elif span and span.is_recording():
            span.end()


# Global instance for convenience
_span_manager = SpanManager()


def start_active_span(name: str, session_id: str | None = None) -> None:
    """Start a new active span using the global span manager."""
    _span_manager.start_active_span(name, session_id)


def end_active_span() -> None:
    """End the most recent active span using the global span manager."""
    try:
        _span_manager.end_active_span()
    except Exception:
        logger.debug("Error ending active span")
        pass
