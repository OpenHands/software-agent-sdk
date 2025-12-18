"""Weave observability integration for OpenHands SDK.

This module provides integration with Weights & Biases Weave for automatic
tracing and observability of agent operations. It leverages Weave's built-in
autopatching to automatically trace all LLM calls made through LiteLLM.

## Key Features

1. **Zero-config LLM tracing**: Just call `init_weave()` and all LiteLLM calls
   are automatically traced - no manual decoration needed!

2. **Automatic integration patching**: Weave automatically patches LiteLLM,
   OpenAI, Anthropic, and 30+ other providers when initialized.

3. **Optional manual tracing**: Use `@weave.op` for custom agent logic that
   you want to trace (tool execution, agent steps, etc.)

4. **Thread grouping**: Group related operations under conversation threads.

## How It Works

The SDK uses LiteLLM for all LLM calls. When you call `init_weave()`:
1. Weave's `implicit_patch()` automatically patches LiteLLM
2. All `litellm.completion()` and `litellm.acompletion()` calls are traced
3. You see full traces in the Weave UI without any code changes!

## Environment Variables

- `WANDB_API_KEY`: Your Weights & Biases API key
- `WEAVE_PROJECT`: The Weave project name (e.g., "my-team/my-project")

## Usage Examples

### Basic Usage (Automatic LLM Tracing)

```python
from openhands.sdk.observability import init_weave
from openhands.sdk import LLM

# Initialize Weave - this automatically traces all LLM calls!
init_weave("my-team/my-project")

# All LLM calls are now automatically traced
llm = LLM(model="gpt-4")
response = llm.completion(messages=[{"role": "user", "content": "Hello!"}])
# ^ This call appears in Weave UI automatically
```

### Custom Function Tracing

```python
import weave
from openhands.sdk.observability import init_weave

init_weave("my-team/my-project")

# Use @weave.op for custom logic you want to trace
@weave.op
def process_agent_step(step: dict) -> dict:
    # Your custom logic here
    return {"processed": True}
```

### Conversation Thread Grouping

```python
from openhands.sdk.observability import init_weave, weave_attributes

init_weave("my-team/my-project")

# Group all operations under a conversation
with weave_attributes(conversation_id="conv-123", user_id="user-456"):
    # All LLM calls and traced functions within this block
    # will be tagged with these attributes
    response = llm.completion(...)
```

See Also:
    - Weave documentation: https://docs.wandb.ai/weave
    - Laminar integration: openhands.sdk.observability.laminar
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

from openhands.sdk.observability.utils import get_env


logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

# Global state
_weave_initialized: bool = False
_weave_client: Any = None


def get_weave_client() -> Any:
    """Get the current Weave client instance.

    Returns:
        The Weave client if initialized, None otherwise.
    """
    return _weave_client


def is_weave_initialized() -> bool:
    """Check if Weave has been initialized.

    Returns:
        True if Weave is initialized and ready for tracing.
    """
    return _weave_initialized


def init_weave(
    project: str | None = None,
    api_key: str | None = None,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    """Initialize Weave for automatic tracing.

    This is the main entry point for enabling Weave observability. When called,
    Weave automatically patches LiteLLM and other supported libraries, so all
    LLM calls are traced without any manual decoration.

    Args:
        project: The Weave project name (e.g., "my-team/my-project").
            If not provided, uses WEAVE_PROJECT environment variable.
        api_key: The Weights & Biases API key. If not provided, uses
            WANDB_API_KEY environment variable.
        settings: Optional dict of Weave settings to configure behavior.
            See Weave documentation for available settings.

    Returns:
        True if initialization was successful, False otherwise.

    Raises:
        ValueError: If no project is specified and WEAVE_PROJECT is not set.

    Example:
        >>> from openhands.sdk.observability import init_weave
        >>> init_weave("my-team/openhands-agent")
        True
        >>> # Now all LiteLLM calls are automatically traced!
    """
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
        # Initialize Weave - this automatically:
        # 1. Patches all already-imported integrations (LiteLLM, OpenAI, etc.)
        # 2. Registers import hooks for future imports
        init_kwargs: dict[str, Any] = {}
        if settings:
            init_kwargs["settings"] = settings

        _weave_client = weave.init(project_name, **init_kwargs)
        _weave_initialized = True

        logger.info(
            f"Weave initialized for project: {project_name}. "
            "All LiteLLM calls will be automatically traced."
        )
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Weave: {e}")
        return False


def maybe_init_weave() -> bool:
    """Initialize Weave if environment variables are configured.

    This is a convenience function that initializes Weave only if both
    WANDB_API_KEY and WEAVE_PROJECT environment variables are set.
    Useful for conditional initialization based on environment.

    Returns:
        True if Weave was initialized (or already was), False otherwise.

    Example:
        >>> import os
        >>> os.environ["WANDB_API_KEY"] = "your-key"
        >>> os.environ["WEAVE_PROJECT"] = "my-team/my-project"
        >>> from openhands.sdk.observability import maybe_init_weave
        >>> maybe_init_weave()  # Initializes automatically
        True
    """
    if _weave_initialized:
        return True

    if not should_enable_weave():
        logger.debug(
            "Weave environment variables not set (WANDB_API_KEY, WEAVE_PROJECT). "
            "Skipping Weave initialization."
        )
        return False

    try:
        return init_weave()
    except ValueError:
        return False


def should_enable_weave() -> bool:
    """Check if Weave should be enabled based on environment variables.

    Returns:
        True if both WANDB_API_KEY and WEAVE_PROJECT are set.
    """
    return bool(get_env("WANDB_API_KEY") and get_env("WEAVE_PROJECT"))


@contextmanager
def weave_attributes(**attributes: Any):
    """Context manager to add attributes to all operations within the block.

    This is useful for grouping related operations (e.g., all events in a
    conversation) or adding metadata to traces.

    Args:
        **attributes: Key-value pairs to attach to all operations.
            Common attributes: conversation_id, user_id, session_id, etc.

    Example:
        >>> with weave_attributes(conversation_id="conv-123", user_id="user-456"):
        ...     # All LLM calls and traced functions here will have these attributes
        ...     response = llm.completion(messages=[...])
    """
    if not _weave_initialized:
        yield
        return

    try:
        import weave
        with weave.attributes(attributes):
            yield
    except Exception as e:
        logger.warning(f"Failed to set weave attributes: {e}")
        yield


@contextmanager
def weave_thread(thread_id: str):
    """Context manager to group operations under a thread.

    This is an alias for weave_attributes(thread_id=...) for convenience
    and backward compatibility.

    Args:
        thread_id: Unique identifier for the thread (e.g., conversation ID).

    Example:
        >>> with weave_thread("conversation-123"):
        ...     # All operations here will be grouped under the same thread
        ...     response = llm.completion(messages=[...])
    """
    with weave_attributes(thread_id=thread_id):
        yield


def get_weave_op():
    """Get the weave.op decorator for manual function tracing.

    Returns the actual weave.op decorator if Weave is initialized,
    otherwise returns a no-op decorator that just returns the function.

    This is useful when you want to trace custom agent logic beyond
    the automatic LLM call tracing.

    Returns:
        The weave.op decorator or a no-op decorator.

    Example:
        >>> from openhands.sdk.observability import init_weave, get_weave_op
        >>> init_weave("my-project")
        >>> weave_op = get_weave_op()
        >>>
        >>> @weave_op
        ... def my_custom_function(x: int) -> int:
        ...     return x * 2
    """
    if not _weave_initialized:
        def noop_decorator(func):
            return func
        return noop_decorator

    try:
        import weave
        return weave.op
    except ImportError:
        def noop_decorator(func):
            return func
        return noop_decorator


def weave_op(
    func: Callable[P, R] | None = None,
    *,
    name: str | None = None,
    call_display_name: str | Callable[..., str] | None = None,
    postprocess_inputs: Callable[..., dict[str, Any]] | None = None,
    postprocess_output: Callable[..., Any] | None = None,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace a function with Weave.

    This is a convenience wrapper around weave.op that handles the case
    when Weave is not initialized (returns the function unchanged).

    Can be used with or without parentheses:
        @weave_op
        def my_func(): ...

        @weave_op(name="custom_name")
        def my_func(): ...

    Args:
        func: The function to decorate (when used without parentheses).
        name: Optional name for the operation. Defaults to function name.
        call_display_name: Display name for the call in the Weave UI.
        postprocess_inputs: Function to transform inputs before logging.
        postprocess_output: Function to transform output before logging.

    Returns:
        The decorated function or a decorator.
    """
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if not _weave_initialized:
            return fn

        try:
            import weave

            op_kwargs: dict[str, Any] = {}
            if name:
                op_kwargs["name"] = name
            if call_display_name:
                op_kwargs["call_display_name"] = call_display_name
            if postprocess_inputs:
                op_kwargs["postprocess_inputs"] = postprocess_inputs
            if postprocess_output:
                op_kwargs["postprocess_output"] = postprocess_output

            if op_kwargs:
                return weave.op(**op_kwargs)(fn)
            return weave.op(fn)
        except Exception as e:
            logger.warning(f"Failed to apply weave.op decorator: {e}")
            return fn

    # Handle both @weave_op and @weave_op(...) syntax
    if func is not None:
        return decorator(func)
    return decorator


def observe_weave(
    name: str | None = None,
    *,
    ignore_inputs: list[str] | None = None,
    ignore_output: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for observing functions with Weave (Laminar-compatible interface).

    This provides a similar interface to the Laminar `observe` decorator,
    making it easier to switch between observability backends.

    Args:
        name: Optional name for the operation.
        ignore_inputs: List of input parameter names to exclude from logging.
        ignore_output: If True, don't log the output.

    Returns:
        A decorator that wraps the function for Weave tracing.

    Example:
        >>> @observe_weave(name="login", ignore_inputs=["password"])
        ... def login(username: str, password: str) -> bool:
        ...     return authenticate(username, password)
    """
    def postprocess_inputs_fn(inputs: dict[str, Any]) -> dict[str, Any]:
        if not ignore_inputs:
            return inputs
        return {k: v for k, v in inputs.items() if k not in ignore_inputs}

    def postprocess_output_fn(output: Any) -> Any:
        if ignore_output:
            return "[output hidden]"
        return output

    return weave_op(
        name=name,
        postprocess_inputs=postprocess_inputs_fn if ignore_inputs else None,
        postprocess_output=postprocess_output_fn if ignore_output else None,
    )


class WeaveSpanManager:
    """Manager for manual span lifecycle control.

    This class provides fine-grained control over span creation and completion,
    useful when automatic decoration is not suitable.

    Note: For most use cases, the automatic LLM tracing and @weave_op decorator
    are sufficient. Use this only when you need explicit span control.

    Example:
        >>> manager = WeaveSpanManager()
        >>> manager.start_span("process_batch", inputs={"batch_size": 100})
        >>> try:
        ...     result = process_batch()
        ...     manager.end_span(output=result)
        ... except Exception as e:
        ...     manager.end_span(error=str(e))
    """

    def __init__(self):
        self._call_stack: list[Any] = []

    def start_span(
        self,
        name: str,
        inputs: dict[str, Any] | None = None,
    ) -> Any:
        """Start a new span.

        Args:
            name: Name of the span/operation.
            inputs: Input parameters to log.

        Returns:
            The span/call object if successful, None otherwise.
        """
        if not _weave_initialized:
            return None

        try:
            import weave

            @weave.op(name=name)
            def _span_op(**kwargs: Any) -> Any:
                pass

            call = _span_op.call(inputs or {})
            self._call_stack.append(call)
            return call
        except Exception as e:
            logger.warning(f"Failed to start weave span: {e}")
            return None

    def end_span(
        self,
        output: Any = None,
        error: str | None = None,
    ) -> None:
        """End the current span.

        Args:
            output: Output value to log.
            error: Error message if the span failed.
        """
        if not self._call_stack:
            return

        try:
            call = self._call_stack.pop()
            if error:
                call.finish(exception=Exception(error))
            else:
                call.finish(output=output)
        except Exception as e:
            logger.warning(f"Failed to end weave span: {e}")


# Global span manager instance for convenience
_global_span_manager = WeaveSpanManager()


def start_weave_span(
    name: str,
    inputs: dict[str, Any] | None = None,
) -> Any:
    """Start a new Weave span using the global manager.

    Args:
        name: Name of the span/operation.
        inputs: Input parameters to log.

    Returns:
        The span/call object if successful, None otherwise.
    """
    return _global_span_manager.start_span(name, inputs)


def end_weave_span(
    output: Any = None,
    error: str | None = None,
) -> None:
    """End the current Weave span using the global manager.

    Args:
        output: Output value to log.
        error: Error message if the span failed.
    """
    _global_span_manager.end_span(output, error)
