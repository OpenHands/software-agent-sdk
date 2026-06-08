import contextlib
import functools
import inspect
import sys
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Final, Literal

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.utils import get_env


if TYPE_CHECKING:
    pass


logger = get_logger(__name__)


# Cache of positive results for should_enable_observability. Once observability
# is enabled (via env vars or a user-side Laminar.initialize() call), it stays
# enabled for the lifetime of the process.
_observability_enabled: bool = False


_OBSERVABILITY_ENV_KEYS: Final[tuple[str, ...]] = (
    "LMNR_PROJECT_API_KEY",
    "OTEL_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


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
    if not should_enable_observability():
        logger.debug(
            "Observability/OTEL environment variables are not set. "
            "Skipping Laminar initialization."
        )
        return

    from lmnr import Instruments, Laminar

    base_url = get_env("LMNR_BASE_URL") or None
    force_http = _get_bool_env("LMNR_FORCE_HTTP")

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
    """Lazy-resolving observe decorator.

    When observability is not enabled, decorated functions run as pass-throughs
    with no `lmnr` import. The first call after observability becomes enabled
    imports `lmnr` and caches the wrapped function.
    """

    def _build_wrapped(func: Any) -> Any:
        from lmnr import observe as laminar_observe

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

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        wrapped: Any = None

        # Branch on async-ness at decoration time so that
        # inspect.iscoroutinefunction(decorated) matches the original. A sync
        # wrapper around an async function would hide its asyncness from
        # callers like run_async that introspect the function.
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **fkwargs: P.kwargs) -> Any:
                nonlocal wrapped
                if wrapped is not None:
                    with _maybe_use_root_span(args):
                        return await wrapped(*args, **fkwargs)
                if not should_enable_observability():
                    return await func(*args, **fkwargs)
                wrapped = _build_wrapped(func)
                with _maybe_use_root_span(args):
                    return await wrapped(*args, **fkwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **fkwargs: P.kwargs) -> R:
            nonlocal wrapped
            if wrapped is not None:
                with _maybe_use_root_span(args):
                    return wrapped(*args, **fkwargs)
            if not should_enable_observability():
                return func(*args, **fkwargs)
            wrapped = _build_wrapped(func)
            with _maybe_use_root_span(args):
                return wrapped(*args, **fkwargs)

        return sync_wrapper

    return decorator


def should_enable_observability() -> bool:
    global _observability_enabled
    if _observability_enabled:
        return True
    if any(get_env(key) for key in _OBSERVABILITY_ENV_KEYS):
        _observability_enabled = True
        return True
    # Only probe Laminar.is_initialized() if the user has already imported
    # lmnr themselves — otherwise importing it here defeats the purpose of
    # lazy loading.
    if "lmnr" in sys.modules:
        from lmnr import Laminar

        if Laminar.is_initialized():
            _observability_enabled = True
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


_ROOT_SPAN_ATTR: Final[str] = "_observability_root_span"


class RootSpan:
    """A long-lived Laminar span owned by a single object (e.g. a Conversation).

    The span is created via ``Laminar.start_span`` (which does NOT attach the
    span to the current OpenTelemetry context). To make the span the parent of
    nested ``@observe``-decorated calls, the ``observe`` wrapper in this module
    re-attaches the span via ``Laminar.use_span`` at every entry point. This
    allows the root span to span across asyncio tasks, threads, and processes
    where naive ``contextvars`` propagation breaks down.

    The ``Laminar.start_active_span`` API was previously used for this purpose
    but its docstring explicitly warns:

        "ending the started span in a different async context yields
         unexpected results. … Use Laminar.start_span + Laminar.use_span
         where possible."

    Empirically, ``start_active_span`` produced trace-context loss for ~60% of
    conversations (orphan ``conversation.send_message`` / ``conversation.run``
    traces with no ``session_id``), so we switched to the recommended pattern.
    """

    def __init__(
        self,
        name: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        from lmnr import Laminar

        # ``start_span`` returns a span without attaching it as the current
        # OTel context; we'll restore it on every entry point via ``use_span``.
        self.span = Laminar.start_span(name)
        if session_id or user_id:
            # ``set_trace_session_id`` / ``set_trace_user_id`` require an
            # active span; briefly enter the span context to apply them.
            with contextlib.suppress(Exception):
                with Laminar.use_span(self.span):
                    if session_id:
                        Laminar.set_trace_session_id(session_id)
                    if user_id:
                        Laminar.set_trace_user_id(user_id)
        self._ended = False

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        try:
            if self.span and self.span.is_recording():
                self.span.end()
        except Exception:
            logger.debug("Error ending observability root span", exc_info=True)


def start_root_span(
    name: str,
    session_id: str | None = None,
    user_id: str | None = None,
) -> RootSpan | None:
    """Create a long-lived root span for an owning object.

    Returns ``None`` if observability is not enabled.
    """
    if not should_enable_observability():
        return None
    try:
        return RootSpan(name, session_id=session_id, user_id=user_id)
    except Exception:
        logger.debug("Failed to create observability root span", exc_info=True)
        return None


def end_root_span(root: RootSpan | None) -> None:
    """End a previously-started root span. Safe to call with ``None``."""
    if root is None:
        return
    root.end()


@contextlib.contextmanager
def _maybe_use_root_span(args: tuple[Any, ...]) -> Iterator[None]:
    """If the first positional arg owns a ``RootSpan``, re-attach it.

    This is what ties ``@observe``-decorated methods (called from arbitrary
    asyncio tasks or threads) back to the conversation's long-lived root span.
    """
    root = _root_span_from_args(args)
    if root is None or root.span is None:
        yield
        return
    try:
        from lmnr import Laminar
    except Exception:
        yield
        return
    try:
        span_context = Laminar.use_span(root.span)
        span_context.__enter__()
    except Exception:
        # Never let an observability error break the wrapped function.
        logger.debug("use_span failed; calling without parent", exc_info=True)
        yield
        return

    exc_info = (None, None, None)
    try:
        yield
    except BaseException:
        exc_info = sys.exc_info()
        raise
    finally:
        with contextlib.suppress(Exception):
            span_context.__exit__(*exc_info)


def _root_span_from_args(args: tuple[Any, ...]) -> RootSpan | None:
    if not args:
        return None
    candidate = getattr(args[0], _ROOT_SPAN_ATTR, None)
    if isinstance(candidate, RootSpan):
        return candidate
    return None


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
        from lmnr import Laminar

        return Laminar.get_laminar_span_context()
    return None
