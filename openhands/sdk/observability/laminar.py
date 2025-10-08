import os
from collections.abc import Callable
from typing import (
    Any,
    Literal,
    cast,
)

from dotenv import dotenv_values
from opentelemetry import context, trace

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.utils import is_package_installed


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
        if is_package_installed("lmnr") and is_package_installed("litellm"):
            import litellm
            from lmnr import Laminar, LaminarLiteLLMCallback

            Laminar.initialize()
            litellm.callbacks.append(LaminarLiteLLMCallback())
        else:
            logger.warning(
                "Observability/OTEL environment variables are set, "
                "but Laminar or LiteLLM is not installed. "
                "Skipping Laminar initialization."
            )
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
        if is_package_installed("lmnr"):
            from lmnr import observe as lmnr_observe

            return lmnr_observe(
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
        else:
            logger.debug("Laminar is not installed. Skipping observe decorator.")
            return cast(Callable[P, R], func)

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
    return False


def start_active_span(name: str) -> trace.Span:
    if is_package_installed("lmnr"):
        return _start_laminar_active_span(name)
    else:
        return _start_otel_active_span(name)


def end_active_span(span: trace.Span):
    if is_package_installed("lmnr"):
        return _end_laminar_active_span(span)
    else:
        return _end_otel_active_span(span)


def _start_otel_active_span(name: str) -> trace.Span:
    tracer = trace.get_tracer(__name__)
    span = tracer.start_span(name=name)
    trace.set_span_in_context(span, context.get_current())
    return span


def _end_otel_active_span(span: trace.Span):
    span.end()


def _start_laminar_active_span(name: str) -> trace.Span:
    from lmnr import Laminar
    from lmnr.opentelemetry_lib.tracing.context import (
        attach_context,
        get_current_context,
        get_token_stack,
        set_token_stack,
    )

    span = Laminar.start_span(name=name)
    current_ctx = get_current_context()
    new_context = trace.set_span_in_context(span, current_ctx)
    token = attach_context(new_context)

    # Store the token for later detachment - tokens are much lighter than contexts
    current_stack = get_token_stack().copy()
    current_stack.append(token)
    set_token_stack(current_stack)

    return span


def _end_laminar_active_span(span: trace.Span):
    if span and span.is_recording():
        span.end()
    try:
        from lmnr import Laminar
        from lmnr.opentelemetry_lib.tracing.context import (
            detach_context,
            get_token_stack,
            set_token_stack,
        )

        Laminar.flush()
        current_stack = get_token_stack().copy()
        if current_stack:
            token = current_stack.pop()
            set_token_stack(current_stack)
            detach_context(token)
    except ImportError:
        # This is expected when the cleanup is done by Python shutting down,
        # e.g. __del__ method on conversation
        logger.debug("Error ending Laminar active span: ImportError")
        pass
