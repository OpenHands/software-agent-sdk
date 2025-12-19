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

2. **Provider Registry**: Observability tools register their context providers,
   allowing easy extension for new tools.

3. **Graceful Degradation**: If no observability tools are enabled, the context
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
from typing import Any, ContextManager, Iterator

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


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
