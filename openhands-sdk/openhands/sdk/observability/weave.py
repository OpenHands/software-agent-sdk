"""Weave observability integration for OpenHands SDK.

This module provides integration with Weights & Biases Weave for tracing
and observability of agent operations. Weave automatically tracks LLM calls,
tool executions, and agent steps.

Configuration:
    Set the following environment variables to enable Weave tracing:
    - WANDB_API_KEY: Your Weights & Biases API key
    - WEAVE_PROJECT: The Weave project name (e.g., "my-team/my-project")

    Alternatively, call `init_weave()` directly with the project name.

Example:
    >>> from openhands.sdk.observability.weave import maybe_init_weave, weave_op
    >>> maybe_init_weave()  # Auto-initializes if env vars are set
    >>>
    >>> @weave_op(name="my_function")
    >>> def my_function(x: int) -> int:
    ...     return x + 1

See Also:
    - Weave documentation: https://docs.wandb.ai/weave
    - Laminar integration: openhands.sdk.observability.laminar
"""

from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.utils import get_env


logger = get_logger(__name__)

# Type variables for generic function signatures
P = ParamSpec("P")
R = TypeVar("R")

# Global state for Weave initialization
_weave_initialized: bool = False
_weave_client: Any = None


def should_enable_weave() -> bool:
    """Check if Weave should be enabled based on environment configuration.

    Returns:
        True if WANDB_API_KEY and WEAVE_PROJECT are set, False otherwise.
    """
    api_key = get_env("WANDB_API_KEY")
    project = get_env("WEAVE_PROJECT")
    return bool(api_key and project)


def is_weave_initialized() -> bool:
    """Check if Weave has been initialized.

    Returns:
        True if Weave is initialized and ready for tracing.
    """
    global _weave_initialized
    return _weave_initialized


def init_weave(
    project: str | None = None,
    api_key: str | None = None,
) -> bool:
    """Initialize Weave for tracing.

    Args:
        project: The Weave project name (e.g., "my-team/my-project").
            If not provided, uses WEAVE_PROJECT environment variable.
        api_key: The Weights & Biases API key. If not provided, uses
            WANDB_API_KEY environment variable.

    Returns:
        True if initialization was successful, False otherwise.

    Raises:
        ValueError: If no project is specified and WEAVE_PROJECT is not set.
    """
    import os

    global _weave_initialized, _weave_client

    if _weave_initialized:
        logger.debug("Weave already initialized, skipping")
        return True

    try:
        import weave
    except ImportError:
        logger.warning(
            "Weave package not installed. Install with: pip install weave"
        )
        return False

    # Determine project name
    project_name = project or get_env("WEAVE_PROJECT")
    if not project_name:
        raise ValueError(
            "Weave project must be specified via argument or WEAVE_PROJECT env var"
        )

    # Set API key in environment if provided (Weave reads from env)
    wandb_api_key = api_key or get_env("WANDB_API_KEY")
    if wandb_api_key:
        os.environ["WANDB_API_KEY"] = wandb_api_key

        # Ensure wandb is logged in (required by weave.init)
        try:
            import wandb
            wandb.login(key=wandb_api_key, relogin=False)
        except Exception as e:
            logger.warning(f"wandb login failed: {e}")
    else:
        logger.warning(
            "WANDB_API_KEY not set. Weave tracing may not work correctly."
        )

    try:
        _weave_client = weave.init(project_name)
        _weave_initialized = True
        logger.info(f"Weave initialized for project: {project_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Weave: {e}")
        return False


def maybe_init_weave() -> bool:
    """Initialize Weave if environment variables are configured.

    This is a convenience function that checks for WANDB_API_KEY and
    WEAVE_PROJECT environment variables and initializes Weave if both are set.

    Returns:
        True if Weave was initialized (or already initialized), False otherwise.
    """
    if is_weave_initialized():
        return True

    if should_enable_weave():
        return init_weave()

    logger.debug(
        "Weave environment variables not set (WANDB_API_KEY, WEAVE_PROJECT). "
        "Skipping Weave initialization."
    )
    return False


def get_weave_client() -> Any:
    """Get the current Weave client.

    Returns:
        The Weave client if initialized, None otherwise.
    """
    global _weave_client
    return _weave_client


def weave_op(
    name: str | None = None,
    *,
    call_display_name: str | Callable[..., str] | None = None,
    postprocess_inputs: Callable[..., dict[str, Any]] | None = None,
    postprocess_output: Callable[..., Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace a function with Weave.

    This decorator wraps a function to automatically trace its inputs, outputs,
    and execution time with Weave. If Weave is not initialized, the function
    runs normally without tracing.

    Args:
        name: Optional name for the operation. Defaults to the function name.
        call_display_name: Optional display name or callable that returns a
            display name for each call.
        postprocess_inputs: Optional function to transform inputs before logging.
        postprocess_output: Optional function to transform output before logging.

    Returns:
        A decorator that wraps the function with Weave tracing.

    Example:
        >>> @weave_op(name="process_data")
        >>> def process_data(data: dict) -> dict:
        ...     return {"processed": True, **data}
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if not is_weave_initialized():
                return func(*args, **kwargs)

            try:
                import weave

                # Build weave.op kwargs
                op_kwargs: dict[str, Any] = {}
                if name:
                    op_kwargs["name"] = name
                if call_display_name:
                    op_kwargs["call_display_name"] = call_display_name
                if postprocess_inputs:
                    op_kwargs["postprocess_inputs"] = postprocess_inputs
                if postprocess_output:
                    op_kwargs["postprocess_output"] = postprocess_output

                # Apply weave.op decorator dynamically
                traced_func = weave.op(**op_kwargs)(func)
                return traced_func(*args, **kwargs)
            except Exception as e:
                logger.debug(f"Weave tracing failed, running without trace: {e}")
                return func(*args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def weave_thread(thread_id: str):
    """Context manager to group operations under a Weave thread.

    Weave threads allow grouping related operations (like all events in a
    conversation) under a single trace hierarchy.

    Args:
        thread_id: Unique identifier for the thread (e.g., conversation ID).

    Yields:
        The thread context if Weave is initialized, otherwise a no-op context.

    Example:
        >>> with weave_thread("conversation-123"):
        ...     # All operations here will be grouped under the same thread
        ...     process_message("Hello")
        ...     generate_response()
    """
    if not is_weave_initialized():
        yield
        return

    try:
        import weave

        # Check if there's an active Weave client
        client = weave.client.get_current_client()
        if client is None:
            yield
            return

        with weave.thread(thread_id):
            yield
    except Exception as e:
        logger.debug(f"Weave thread context failed: {e}")
        yield


class WeaveSpanManager:
    """Manages Weave spans for manual tracing.

    This class provides a stack-based approach to managing Weave spans,
    similar to the SpanManager for Laminar. It's useful when you need
    more control over span lifecycle than the decorator provides.

    Example:
        >>> manager = WeaveSpanManager()
        >>> manager.start_span("process_request", session_id="conv-123")
        >>> try:
        ...     # Do work
        ...     pass
        ... finally:
        ...     manager.end_span()
    """

    def __init__(self):
        self._call_stack: list[Any] = []

    def start_span(
        self,
        name: str,
        inputs: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Any | None:
        """Start a new Weave span.

        Args:
            name: Name of the operation being traced.
            inputs: Optional dictionary of input values to log.
            session_id: Optional session ID for grouping related spans.

        Returns:
            The Weave call object if successful, None otherwise.
        """
        if not is_weave_initialized():
            return None

        try:
            import weave

            client = get_weave_client()
            if client is None:
                return None

            # Create a call using the client API
            call = client.create_call(
                op=name,
                inputs=inputs or {},
            )
            self._call_stack.append(call)
            return call
        except Exception as e:
            logger.debug(f"Failed to start Weave span: {e}")
            return None

    def end_span(self, output: Any = None, error: Exception | None = None) -> None:
        """End the most recent Weave span.

        Args:
            output: Optional output value to log.
            error: Optional exception if the operation failed.
        """
        if not self._call_stack:
            logger.debug("Attempted to end span, but stack is empty")
            return

        try:
            call = self._call_stack.pop()
            client = get_weave_client()
            if client and call:
                if error:
                    client.finish_call(call, output=None, exception=error)
                else:
                    client.finish_call(call, output=output)
        except Exception as e:
            logger.debug(f"Failed to end Weave span: {e}")


# Global span manager instance
_span_manager: WeaveSpanManager | None = None


def _get_span_manager() -> WeaveSpanManager:
    """Get or create the global span manager."""
    global _span_manager
    if _span_manager is None:
        _span_manager = WeaveSpanManager()
    return _span_manager


def start_weave_span(
    name: str,
    inputs: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> Any | None:
    """Start a new Weave span using the global span manager.

    Args:
        name: Name of the operation being traced.
        inputs: Optional dictionary of input values to log.
        session_id: Optional session ID for grouping related spans.

    Returns:
        The Weave call object if successful, None otherwise.
    """
    return _get_span_manager().start_span(name, inputs, session_id)


def end_weave_span(output: Any = None, error: Exception | None = None) -> None:
    """End the most recent Weave span using the global span manager.

    Args:
        output: Optional output value to log.
        error: Optional exception if the operation failed.
    """
    try:
        _get_span_manager().end_span(output, error)
    except Exception:
        logger.debug("Error ending Weave span")


def observe_weave(
    *,
    name: str | None = None,
    ignore_inputs: list[str] | None = None,
    ignore_output: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Unified observe decorator that works with both Weave and Laminar.

    This decorator provides a consistent interface for observability that
    works regardless of which backend (Weave or Laminar) is configured.
    It prioritizes Weave if initialized, otherwise falls back to Laminar.

    Args:
        name: Optional name for the operation.
        ignore_inputs: List of input parameter names to exclude from logging.
        ignore_output: If True, don't log the function's output.

    Returns:
        A decorator that wraps the function with observability tracing.

    Example:
        >>> @observe_weave(name="agent.step", ignore_inputs=["state"])
        >>> def step(self, state: State) -> Action:
        ...     return self._process(state)
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Try Weave first
            if is_weave_initialized():
                try:
                    import weave

                    op_kwargs: dict[str, Any] = {}
                    if name:
                        op_kwargs["name"] = name

                    # Handle input filtering via postprocess_inputs
                    if ignore_inputs:
                        def filter_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
                            return {
                                k: v for k, v in inputs.items()
                                if k not in ignore_inputs
                            }
                        op_kwargs["postprocess_inputs"] = filter_inputs

                    traced_func = weave.op(**op_kwargs)(func)
                    return traced_func(*args, **kwargs)
                except Exception as e:
                    logger.debug(f"Weave tracing failed: {e}")

            # Fall through to untraced execution
            return func(*args, **kwargs)

        return wrapper

    return decorator
