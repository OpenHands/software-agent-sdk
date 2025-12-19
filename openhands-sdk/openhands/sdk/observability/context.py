"""Generic observability context management for the OpenHands SDK.

This module provides a unified interface for managing observability contexts
across multiple observability tools (Weave, Laminar, etc.). It allows the SDK
to use a single API that automatically composes context managers from all
enabled observability providers.

## Design Philosophy

The SDK should be agnostic to which observability tools are enabled. This module
provides:

1. **Unified Context Managers**: A single `get_conversation_context()` function
   that returns a composed context manager for all enabled tools.

2. **Tool Tracing**: A `trace_tool_call()` decorator/context manager for tracing
   tool executions across all enabled observability tools.

3. **Provider Registry**: Observability tools register their context providers,
   allowing easy extension for new tools.

4. **Graceful Degradation**: If no observability tools are enabled, the context
   managers are no-ops (nullcontext).

## Usage

In LocalConversation.run():
```python
from openhands.sdk.observability.context import get_conversation_context

def run(self):
    with get_conversation_context(str(self.id)):
        # All operations here are traced by all enabled observability tools
        ...
```

For tool execution tracing:
```python
from openhands.sdk.observability.context import trace_tool_call

# As a decorator
@trace_tool_call(tool_name="my_tool")
def execute_tool(action):
    ...

# As a context manager
with trace_tool_call(tool_name="my_tool", inputs={"arg": "value"}):
    result = tool.execute(action)
```

## Adding New Observability Providers

To add a new observability tool:

1. Create a function that returns a context manager for conversation threading
2. Register it with `register_conversation_context_provider()`

```python
from openhands.sdk.observability.context import register_conversation_context_provider

def get_my_tool_context(conversation_id: str):
    if not is_my_tool_initialized():
        return nullcontext()
    return my_tool.thread(conversation_id)

register_conversation_context_provider(get_my_tool_context)
```
"""

from collections.abc import Callable
from contextlib import ExitStack, contextmanager, nullcontext
from functools import wraps
from typing import Any, ContextManager, Iterator, ParamSpec, TypeVar

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


# Type alias for context provider functions
ConversationContextProvider = Callable[[str], ContextManager[Any]]

# Registry of conversation context providers
_conversation_context_providers: list[ConversationContextProvider] = []


def register_conversation_context_provider(
    provider: ConversationContextProvider,
) -> None:
    """Register a conversation context provider.

    Context providers are functions that take a conversation_id and return
    a context manager. They are called in order of registration.

    Args:
        provider: A function that takes a conversation_id string and returns
                 a context manager. Should return nullcontext() if the
                 observability tool is not initialized.

    Example:
        ```python
        def get_my_tool_context(conversation_id: str):
            if not is_my_tool_initialized():
                return nullcontext()
            return my_tool.thread(conversation_id)

        register_conversation_context_provider(get_my_tool_context)
        ```
    """
    if provider not in _conversation_context_providers:
        _conversation_context_providers.append(provider)
        logger.debug(f"Registered conversation context provider: {provider.__name__}")


def unregister_conversation_context_provider(
    provider: ConversationContextProvider,
) -> None:
    """Unregister a conversation context provider.

    Args:
        provider: The provider function to unregister.
    """
    if provider in _conversation_context_providers:
        _conversation_context_providers.remove(provider)
        logger.debug(f"Unregistered conversation context provider: {provider.__name__}")


def clear_conversation_context_providers() -> None:
    """Clear all registered conversation context providers.

    Useful for testing or resetting the observability state.
    """
    _conversation_context_providers.clear()
    logger.debug("Cleared all conversation context providers")


@contextmanager
def get_conversation_context(conversation_id: str) -> Iterator[None]:
    """Get a composed context manager for all enabled observability tools.

    This function returns a context manager that wraps all registered
    observability context providers. When entered, it enters all provider
    contexts in order. When exited, it exits them in reverse order.

    If no providers are registered or all providers return nullcontext,
    this is effectively a no-op.

    Args:
        conversation_id: The conversation ID to use for threading/grouping.

    Yields:
        None

    Example:
        ```python
        with get_conversation_context("conv-123"):
            # All operations here are traced by all enabled observability tools
            agent.step(...)
        ```
    """
    if not _conversation_context_providers:
        yield
        return

    # Use ExitStack to compose multiple context managers
    with ExitStack() as stack:
        for provider in _conversation_context_providers:
            try:
                ctx = provider(conversation_id)
                stack.enter_context(ctx)
            except Exception as e:
                # Log but don't fail - observability should not break the agent
                logger.debug(
                    f"Error entering context from provider {provider.__name__}: {e}"
                )
        yield


# =============================================================================
# Built-in Provider Registrations
# =============================================================================
# These are registered when the module is imported. Each provider checks if
# its tool is initialized before returning a real context manager.


def _get_weave_conversation_context(conversation_id: str) -> ContextManager[Any]:
    """Weave conversation context provider.

    Returns a weave.thread() context manager if Weave is initialized,
    otherwise returns nullcontext().
    """
    try:
        from openhands.sdk.observability.weave import is_weave_initialized

        if not is_weave_initialized():
            return nullcontext()

        import weave
        return weave.thread(conversation_id)
    except ImportError:
        return nullcontext()
    except Exception:
        return nullcontext()


def _get_laminar_conversation_context(conversation_id: str) -> ContextManager[Any]:
    """Laminar conversation context provider.

    Returns a Laminar span context if Laminar is initialized,
    otherwise returns nullcontext().

    Note: Laminar uses OpenTelemetry spans rather than threads, so we create
    a span with the conversation_id as the session_id.
    """
    try:
        from openhands.sdk.observability.laminar import should_enable_observability

        if not should_enable_observability():
            return nullcontext()

        from lmnr import Laminar

        @contextmanager
        def laminar_conversation_context():
            span = Laminar.start_active_span(f"conversation:{conversation_id}")
            Laminar.set_trace_session_id(conversation_id)
            try:
                yield
            finally:
                if span and span.is_recording():
                    span.end()

        return laminar_conversation_context()
    except ImportError:
        return nullcontext()
    except Exception:
        return nullcontext()


# Register built-in providers
register_conversation_context_provider(_get_weave_conversation_context)
register_conversation_context_provider(_get_laminar_conversation_context)


# =============================================================================
# Tool Call Tracing
# =============================================================================
# Unified tracing for tool executions across all observability tools.


ToolTraceProvider = Callable[[str, dict[str, Any] | None], ContextManager[Any]]

# Registry of tool trace providers
_tool_trace_providers: list[ToolTraceProvider] = []


def register_tool_trace_provider(provider: ToolTraceProvider) -> None:
    """Register a tool trace provider.

    Tool trace providers are functions that take a tool_name and optional
    inputs dict, and return a context manager for tracing the tool execution.

    Args:
        provider: A function that takes (tool_name, inputs) and returns
                 a context manager. Should return nullcontext() if the
                 observability tool is not initialized.
    """
    if provider not in _tool_trace_providers:
        _tool_trace_providers.append(provider)
        logger.debug(f"Registered tool trace provider: {provider.__name__}")


def unregister_tool_trace_provider(provider: ToolTraceProvider) -> None:
    """Unregister a tool trace provider."""
    if provider in _tool_trace_providers:
        _tool_trace_providers.remove(provider)
        logger.debug(f"Unregistered tool trace provider: {provider.__name__}")


def clear_tool_trace_providers() -> None:
    """Clear all registered tool trace providers."""
    _tool_trace_providers.clear()
    logger.debug("Cleared all tool trace providers")


@contextmanager
def trace_tool_call(
    tool_name: str,
    inputs: dict[str, Any] | None = None,
    tool_type: str = "TOOL",
) -> Iterator[None]:
    """Trace a tool call across all enabled observability tools.

    This context manager wraps tool executions with tracing from all
    registered observability providers (Weave, Laminar, etc.).

    Args:
        tool_name: The name of the tool being executed.
        inputs: Optional dict of input arguments to the tool.
        tool_type: The type of tool (e.g., "TOOL", "MCP_TOOL"). Used for
                  categorization in observability UIs.

    Yields:
        None

    Example:
        ```python
        with trace_tool_call("bash", inputs={"command": "ls -la"}):
            result = bash_tool.execute(action)
        ```
    """
    if not _tool_trace_providers:
        yield
        return

    with ExitStack() as stack:
        for provider in _tool_trace_providers:
            try:
                ctx = provider(tool_name, inputs)
                stack.enter_context(ctx)
            except Exception as e:
                logger.debug(
                    f"Error entering tool trace from provider {provider.__name__}: {e}"
                )
        yield


def traced_tool(
    tool_name: str | None = None,
    tool_type: str = "TOOL",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace tool execution functions.

    This decorator wraps a function with tool tracing from all registered
    observability providers. It automatically captures the function's
    arguments as inputs.

    Args:
        tool_name: The name of the tool. If None, uses the function name.
        tool_type: The type of tool (e.g., "TOOL", "MCP_TOOL").

    Returns:
        A decorator that wraps the function with tool tracing.

    Example:
        ```python
        @traced_tool(tool_name="bash")
        def execute_bash(command: str) -> str:
            ...

        # Or with automatic name detection
        @traced_tool()
        def my_tool(arg1, arg2):
            ...
        ```
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        name = tool_name or func.__name__

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Capture inputs from kwargs (args are harder to name)
            inputs = dict(kwargs) if kwargs else None
            with trace_tool_call(name, inputs=inputs, tool_type=tool_type):
                return func(*args, **kwargs)

        return wrapper

    return decorator


# =============================================================================
# Built-in Tool Trace Providers
# =============================================================================


def _get_weave_tool_trace(
    tool_name: str, inputs: dict[str, Any] | None
) -> ContextManager[Any]:
    """Weave tool trace provider.

    Uses weave.attributes() to add tool metadata to the current span.
    The actual tracing is done by Weave's autopatching of the underlying
    operations (LLM calls, etc.).
    """
    try:
        from openhands.sdk.observability.weave import is_weave_initialized

        if not is_weave_initialized():
            return nullcontext()

        import weave

        # Use weave.attributes to add tool metadata to the trace
        attributes = {"tool_name": tool_name, "tool_type": "TOOL"}
        if inputs:
            # Sanitize inputs - convert non-serializable types to strings
            safe_inputs = {}
            for k, v in inputs.items():
                try:
                    # Test if it's JSON serializable
                    import json
                    json.dumps(v)
                    safe_inputs[k] = v
                except (TypeError, ValueError):
                    safe_inputs[k] = str(v)
            attributes["tool_inputs"] = safe_inputs

        return weave.attributes(attributes)
    except ImportError:
        return nullcontext()
    except Exception:
        return nullcontext()


def _get_laminar_tool_trace(
    tool_name: str, inputs: dict[str, Any] | None  # noqa: ARG001
) -> ContextManager[Any]:
    """Laminar tool trace provider.

    Creates a Laminar span for the tool execution.
    Note: Laminar's @observe decorator is typically used directly,
    but this provides a context manager alternative.
    """
    try:
        from openhands.sdk.observability.laminar import should_enable_observability

        if not should_enable_observability():
            return nullcontext()

        from lmnr import Laminar

        @contextmanager
        def laminar_tool_trace():
            span = Laminar.start_active_span(f"tool:{tool_name}")
            try:
                yield
            finally:
                if span and span.is_recording():
                    span.end()

        return laminar_tool_trace()
    except ImportError:
        return nullcontext()
    except Exception:
        return nullcontext()


# Register built-in tool trace providers
register_tool_trace_provider(_get_weave_tool_trace)
register_tool_trace_provider(_get_laminar_tool_trace)


# =============================================================================
# MCP-Specific Tracing
# =============================================================================


@contextmanager
def trace_mcp_list_tools(server_name: str | None = None) -> Iterator[None]:
    """Trace MCP tool listing operations.

    Args:
        server_name: Optional name of the MCP server being queried.

    Yields:
        None
    """
    tool_name = f"mcp:list_tools:{server_name}" if server_name else "mcp:list_tools"
    with trace_tool_call(tool_name, tool_type="MCP_LIST"):
        yield


@contextmanager
def trace_mcp_call_tool(
    tool_name: str,
    server_name: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Trace MCP tool call operations.

    Args:
        tool_name: The name of the MCP tool being called.
        server_name: Optional name of the MCP server.
        inputs: Optional dict of input arguments.

    Yields:
        None
    """
    full_name = f"mcp:{server_name}:{tool_name}" if server_name else f"mcp:{tool_name}"
    with trace_tool_call(full_name, inputs=inputs, tool_type="MCP_TOOL"):
        yield
