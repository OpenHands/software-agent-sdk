from collections.abc import Callable
from typing import (
    Any,
    Literal,
)

import litellm
from lmnr import (
    Instruments,
    Laminar,
    LaminarLiteLLMCallback,
    observe as laminar_observe,
)
from opentelemetry import trace

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.utils import get_env


logger = get_logger(__name__)


def _get_int_env(key: str) -> int | None:
    """Read an environment variable as an optional int."""
    val = get_env(key)
    if val is not None and val != "":
        try:
            return int(val)
        except ValueError:
            logger.warning("%s must be an integer, got %r", key, val)
            return None
    return None


def _get_bool_env(key: str) -> bool:
    """Read an environment variable as a boolean.

    Returns True if the value is 'true', '1', 'yes', 'on' (case-insensitive).
    Returns False otherwise.
    """
    val = get_env(key)
    if val is None:
        return False
    return val.lower() in ("true", "1", "yes", "on")


def maybe_init_laminar():
    """Initialize Laminar if the environment variables are set.

    Example configuration:

    ```bash
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4317/v1/traces

    # comma separated, key=value url-encoded pairs
    OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20<KEY>,X-Key=<CUSTOM_VALUE>"

    # grpc is assumed if not specified
    OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf # or grpc/protobuf
    # or
    OTEL_EXPORTER=otlp_http # or otlp_grpc
    ```

    For self-hosted Laminar, set the base URL and ports via environment variables:
    LMNR_BASE_URL=https://api.lmnr.ai  # optional, defaults to https://api.lmnr.ai
    LMNR_HTTP_PORT=8000
    LMNR_GRPC_PORT=8001

    To force HTTP instead of gRPC for Laminar communication:
    LMNR_FORCE_HTTP=true  # or 1, yes, on
    """
    base_url = get_env("LMNR_BASE_URL") or None
    force_http = _get_bool_env("LMNR_FORCE_HTTP")

    if should_enable_observability():
        if _is_otel_backend_laminar():
            Laminar.initialize(
                base_url=base_url,
                http_port=_get_int_env("LMNR_HTTP_PORT"),
                grpc_port=_get_int_env("LMNR_GRPC_PORT"),
                force_http=force_http,
            )
        else:
            # Do not enable browser session replays for non-laminar backends
            Laminar.initialize(
                disabled_instruments=[
                    Instruments.BROWSER_USE_SESSION,
                    Instruments.PATCHRIGHT,
                    Instruments.PLAYWRIGHT,
                ],
                force_http=force_http,
            )
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
    rollout_entrypoint: bool = False,
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
            rollout_entrypoint=rollout_entrypoint,
            **kwargs,
        )(func)

    return decorator


def should_enable_observability():
    keys = [
        "LMNR_PROJECT_API_KEY",
        "OTEL_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ]
    if any(get_env(key) for key in keys):
        return True
    if Laminar.is_initialized():
        return True
    return False


def _is_otel_backend_laminar():
    """Simple heuristic to check if the OTEL backend is Laminar.
    Caveat: This will still be True if another backend uses the same
    authentication scheme, and the user uses LMNR_PROJECT_API_KEY
    instead of OTEL_HEADERS to authenticate.
    """
    key = get_env("LMNR_PROJECT_API_KEY")
    return key is not None and key != ""


class SpanManager:
    """Manages a stack of active spans and their associated tokens."""

    def __init__(self):
        self._stack: list[trace.Span] = []

    def start_active_span(self, name: str, session_id: str | None = None) -> None:
        """Start a new active span and push it to the stack."""
        span = Laminar.start_active_span(name)
        if session_id:
            Laminar.set_trace_session_id(session_id)
        self._stack.append(span)

    def end_active_span(self) -> None:
        """End the most recent active span by popping it from the stack."""
        if not self._stack:
            logger.warning("Attempted to end active span, but stack is empty")
            return

        try:
            span = self._stack.pop()
            if span and span.is_recording():
                span.end()
        except IndexError:
            logger.warning("Attempted to end active span, but stack is empty")
            return


_span_manager: SpanManager | None = None


def _get_span_manager() -> SpanManager:
    global _span_manager
    if _span_manager is None:
        _span_manager = SpanManager()
    return _span_manager


def start_active_span(name: str, session_id: str | None = None) -> None:
    """Start a new active span using the global span manager."""
    _get_span_manager().start_active_span(name, session_id)


def end_active_span() -> None:
    """End the most recent active span using the global span manager."""
    try:
        _get_span_manager().end_active_span()
    except Exception:
        logger.debug("Error ending active span")
        pass


def init_laminar_for_external():
    """Initialize Laminar for external callers and return parent span context.

    This is a convenience function for integrations (e.g., GitHub, Slack webhooks)
    that need to:
    1. Initialize Laminar if env vars are set (via maybe_init_laminar)
    2. Capture the parent span context from the external trigger

    Returns:
        The parent span context if observability is enabled, None otherwise.

    Example:
        ```python
        from openhands.sdk.observability import init_laminar_for_external
        from lmnr import Laminar

        # At the start of handling an external event (webhook, etc.)
        laminar_span_context = init_laminar_for_external()

        if laminar_span_context:
            with Laminar.start_as_current_span(
                name='my-integration',
                parent_span_context=laminar_span_context,
            ):
                # Do work - traces will be children of the external trigger
                await do_something()
        else:
            await do_something()
        ```
    """
    maybe_init_laminar()
    if should_enable_observability():
        return Laminar.get_laminar_span_context()
    return None
