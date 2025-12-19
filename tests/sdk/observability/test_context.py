"""Tests for the generic observability context module."""

import pytest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from openhands.sdk.observability.context import (
    clear_conversation_context_providers,
    get_conversation_context,
    register_conversation_context_provider,
    unregister_conversation_context_provider,
    _conversation_context_providers,
    # Tool tracing
    clear_tool_trace_providers,
    register_tool_trace_provider,
    unregister_tool_trace_provider,
    trace_tool_call,
    traced_tool,
    trace_mcp_list_tools,
    trace_mcp_call_tool,
    _tool_trace_providers,
)


class TestConversationContextProviderRegistry:
    """Tests for the provider registry functions."""

    def setup_method(self):
        """Clear providers before each test."""
        # Store original providers
        self._original_providers = _conversation_context_providers.copy()
        clear_conversation_context_providers()

    def teardown_method(self):
        """Restore original providers after each test."""
        clear_conversation_context_providers()
        for provider in self._original_providers:
            register_conversation_context_provider(provider)

    def test_register_provider(self):
        """Test registering a new provider."""
        def my_provider(conversation_id: str):
            return nullcontext()

        register_conversation_context_provider(my_provider)
        assert my_provider in _conversation_context_providers

    def test_register_provider_no_duplicates(self):
        """Test that registering the same provider twice doesn't create duplicates."""
        def my_provider(conversation_id: str):
            return nullcontext()

        register_conversation_context_provider(my_provider)
        register_conversation_context_provider(my_provider)
        assert _conversation_context_providers.count(my_provider) == 1

    def test_unregister_provider(self):
        """Test unregistering a provider."""
        def my_provider(conversation_id: str):
            return nullcontext()

        register_conversation_context_provider(my_provider)
        assert my_provider in _conversation_context_providers

        unregister_conversation_context_provider(my_provider)
        assert my_provider not in _conversation_context_providers

    def test_unregister_nonexistent_provider(self):
        """Test unregistering a provider that was never registered."""
        def my_provider(conversation_id: str):
            return nullcontext()

        # Should not raise
        unregister_conversation_context_provider(my_provider)

    def test_clear_providers(self):
        """Test clearing all providers."""
        def provider1(conversation_id: str):
            return nullcontext()

        def provider2(conversation_id: str):
            return nullcontext()

        register_conversation_context_provider(provider1)
        register_conversation_context_provider(provider2)
        assert len(_conversation_context_providers) == 2

        clear_conversation_context_providers()
        assert len(_conversation_context_providers) == 0


class TestGetConversationContext:
    """Tests for the get_conversation_context function."""

    def setup_method(self):
        """Clear providers before each test."""
        self._original_providers = _conversation_context_providers.copy()
        clear_conversation_context_providers()

    def teardown_method(self):
        """Restore original providers after each test."""
        clear_conversation_context_providers()
        for provider in self._original_providers:
            register_conversation_context_provider(provider)

    def test_no_providers_is_noop(self):
        """Test that with no providers, the context is a no-op."""
        executed = False

        with get_conversation_context("test-conv"):
            executed = True

        assert executed

    def test_single_provider_called(self):
        """Test that a single provider is called with the conversation ID."""
        called_with = []

        def my_provider(conversation_id: str):
            called_with.append(conversation_id)
            return nullcontext()

        register_conversation_context_provider(my_provider)

        with get_conversation_context("test-conv-123"):
            pass

        assert called_with == ["test-conv-123"]

    def test_multiple_providers_called_in_order(self):
        """Test that multiple providers are called in registration order."""
        call_order = []

        def provider1(conversation_id: str):
            call_order.append("provider1")
            return nullcontext()

        def provider2(conversation_id: str):
            call_order.append("provider2")
            return nullcontext()

        register_conversation_context_provider(provider1)
        register_conversation_context_provider(provider2)

        with get_conversation_context("test-conv"):
            pass

        assert call_order == ["provider1", "provider2"]

    def test_provider_exception_does_not_break_others(self):
        """Test that an exception in one provider doesn't prevent others."""
        call_order = []

        def failing_provider(conversation_id: str):
            raise RuntimeError("Provider failed")

        def working_provider(conversation_id: str):
            call_order.append("working")
            return nullcontext()

        register_conversation_context_provider(failing_provider)
        register_conversation_context_provider(working_provider)

        # Should not raise
        with get_conversation_context("test-conv"):
            pass

        assert call_order == ["working"]

    def test_context_manager_enter_exit_called(self):
        """Test that context manager __enter__ and __exit__ are called."""
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=None)
        mock_cm.__exit__ = MagicMock(return_value=None)

        def my_provider(conversation_id: str):
            return mock_cm

        register_conversation_context_provider(my_provider)

        with get_conversation_context("test-conv"):
            mock_cm.__enter__.assert_called_once()

        mock_cm.__exit__.assert_called_once()


class TestBuiltInProviders:
    """Tests for the built-in Weave and Laminar providers."""

    def test_weave_provider_returns_nullcontext_when_not_initialized(self):
        """Test that Weave provider returns nullcontext when Weave is not initialized."""
        from openhands.sdk.observability.context import _get_weave_conversation_context

        with patch(
            "openhands.sdk.observability.weave.is_weave_initialized",
            return_value=False,
        ):
            ctx = _get_weave_conversation_context("test-conv")
            # nullcontext() returns a different instance each time, so check type name
            assert type(ctx).__name__ == "nullcontext"

    def test_laminar_provider_returns_nullcontext_when_not_initialized(self):
        """Test that Laminar provider returns nullcontext when Laminar is not initialized."""
        from openhands.sdk.observability.context import (
            _get_laminar_conversation_context,
        )

        with patch(
            "openhands.sdk.observability.laminar.should_enable_observability",
            return_value=False,
        ):
            ctx = _get_laminar_conversation_context("test-conv")
            # nullcontext() returns a different instance each time, so check type name
            assert type(ctx).__name__ == "nullcontext"


class TestIntegration:
    """Integration tests for the observability context system."""

    def test_providers_auto_registered_on_import(self):
        """Test that built-in providers are registered when module is imported."""
        # Re-import to trigger registration
        from openhands.sdk.observability import context

        # The module should have registered the built-in providers
        # We check by looking for the provider functions
        provider_names = [p.__name__ for p in context._conversation_context_providers]
        assert "_get_weave_conversation_context" in provider_names
        assert "_get_laminar_conversation_context" in provider_names

    def test_custom_provider_works_with_builtins(self):
        """Test that custom providers work alongside built-in ones."""
        custom_called = []

        def custom_provider(conversation_id: str):
            custom_called.append(conversation_id)
            return nullcontext()

        register_conversation_context_provider(custom_provider)

        try:
            with get_conversation_context("test-conv"):
                pass

            assert "test-conv" in custom_called
        finally:
            unregister_conversation_context_provider(custom_provider)


# =============================================================================
# Tool Tracing Tests
# =============================================================================


class TestToolTraceProviderRegistry:
    """Tests for the tool trace provider registry functions."""

    def setup_method(self):
        """Store original providers before each test."""
        self._original_providers = _tool_trace_providers.copy()
        clear_tool_trace_providers()

    def teardown_method(self):
        """Restore original providers after each test."""
        clear_tool_trace_providers()
        for provider in self._original_providers:
            register_tool_trace_provider(provider)

    def test_register_tool_trace_provider(self):
        """Test registering a new tool trace provider."""
        def my_provider(tool_name: str, inputs):
            return nullcontext()

        register_tool_trace_provider(my_provider)
        assert my_provider in _tool_trace_providers

    def test_register_tool_trace_provider_no_duplicates(self):
        """Test that registering the same provider twice doesn't create duplicates."""
        def my_provider(tool_name: str, inputs):
            return nullcontext()

        register_tool_trace_provider(my_provider)
        register_tool_trace_provider(my_provider)
        assert _tool_trace_providers.count(my_provider) == 1

    def test_unregister_tool_trace_provider(self):
        """Test unregistering a tool trace provider."""
        def my_provider(tool_name: str, inputs):
            return nullcontext()

        register_tool_trace_provider(my_provider)
        assert my_provider in _tool_trace_providers

        unregister_tool_trace_provider(my_provider)
        assert my_provider not in _tool_trace_providers

    def test_clear_tool_trace_providers(self):
        """Test clearing all tool trace providers."""
        def provider1(tool_name: str, inputs):
            return nullcontext()

        def provider2(tool_name: str, inputs):
            return nullcontext()

        register_tool_trace_provider(provider1)
        register_tool_trace_provider(provider2)
        assert len(_tool_trace_providers) == 2

        clear_tool_trace_providers()
        assert len(_tool_trace_providers) == 0


class TestTraceToolCall:
    """Tests for the trace_tool_call context manager."""

    def setup_method(self):
        """Store original providers before each test."""
        self._original_providers = _tool_trace_providers.copy()
        clear_tool_trace_providers()

    def teardown_method(self):
        """Restore original providers after each test."""
        clear_tool_trace_providers()
        for provider in self._original_providers:
            register_tool_trace_provider(provider)

    def test_no_providers_is_noop(self):
        """Test that with no providers, the context is a no-op."""
        executed = False

        with trace_tool_call("test-tool"):
            executed = True

        assert executed

    def test_single_provider_called(self):
        """Test that a single provider is called with tool name and inputs."""
        called_with = []

        def my_provider(tool_name: str, inputs):
            called_with.append((tool_name, inputs))
            return nullcontext()

        register_tool_trace_provider(my_provider)

        with trace_tool_call("bash", inputs={"command": "ls"}):
            pass

        assert called_with == [("bash", {"command": "ls"})]

    def test_multiple_providers_called(self):
        """Test that multiple providers are called."""
        call_order = []

        def provider1(tool_name: str, inputs):
            call_order.append("provider1")
            return nullcontext()

        def provider2(tool_name: str, inputs):
            call_order.append("provider2")
            return nullcontext()

        register_tool_trace_provider(provider1)
        register_tool_trace_provider(provider2)

        with trace_tool_call("test-tool"):
            pass

        assert call_order == ["provider1", "provider2"]

    def test_provider_exception_does_not_break_others(self):
        """Test that an exception in one provider doesn't prevent others."""
        call_order = []

        def failing_provider(tool_name: str, inputs):
            raise RuntimeError("Provider failed")

        def working_provider(tool_name: str, inputs):
            call_order.append("working")
            return nullcontext()

        register_tool_trace_provider(failing_provider)
        register_tool_trace_provider(working_provider)

        # Should not raise
        with trace_tool_call("test-tool"):
            pass

        assert call_order == ["working"]


class TestTracedToolDecorator:
    """Tests for the @traced_tool decorator."""

    def setup_method(self):
        """Store original providers before each test."""
        self._original_providers = _tool_trace_providers.copy()
        clear_tool_trace_providers()

    def teardown_method(self):
        """Restore original providers after each test."""
        clear_tool_trace_providers()
        for provider in self._original_providers:
            register_tool_trace_provider(provider)

    def test_traced_tool_with_explicit_name(self):
        """Test @traced_tool with explicit tool name."""
        traced_calls = []

        def my_provider(tool_name: str, inputs):
            traced_calls.append(tool_name)
            return nullcontext()

        register_tool_trace_provider(my_provider)

        @traced_tool(tool_name="my_custom_tool")
        def some_function(x, y):
            return x + y

        result = some_function(1, 2)
        assert result == 3
        assert traced_calls == ["my_custom_tool"]

    def test_traced_tool_with_auto_name(self):
        """Test @traced_tool with automatic name detection."""
        traced_calls = []

        def my_provider(tool_name: str, inputs):
            traced_calls.append(tool_name)
            return nullcontext()

        register_tool_trace_provider(my_provider)

        @traced_tool()
        def auto_named_function(x):
            return x * 2

        result = auto_named_function(5)
        assert result == 10
        assert traced_calls == ["auto_named_function"]

    def test_traced_tool_captures_kwargs(self):
        """Test that @traced_tool captures kwargs as inputs."""
        traced_inputs = []

        def my_provider(tool_name: str, inputs):
            traced_inputs.append(inputs)
            return nullcontext()

        register_tool_trace_provider(my_provider)

        @traced_tool(tool_name="test")
        def func_with_kwargs(a, b=10, c="hello"):
            return f"{a}-{b}-{c}"

        result = func_with_kwargs(1, b=20, c="world")
        assert result == "1-20-world"
        assert traced_inputs == [{"b": 20, "c": "world"}]


class TestMCPTracing:
    """Tests for MCP-specific tracing functions."""

    def setup_method(self):
        """Store original providers before each test."""
        self._original_providers = _tool_trace_providers.copy()
        clear_tool_trace_providers()

    def teardown_method(self):
        """Restore original providers after each test."""
        clear_tool_trace_providers()
        for provider in self._original_providers:
            register_tool_trace_provider(provider)

    def test_trace_mcp_list_tools(self):
        """Test trace_mcp_list_tools context manager."""
        traced_calls = []

        def my_provider(tool_name: str, inputs):
            traced_calls.append(tool_name)
            return nullcontext()

        register_tool_trace_provider(my_provider)

        with trace_mcp_list_tools():
            pass

        assert traced_calls == ["mcp:list_tools"]

    def test_trace_mcp_list_tools_with_server_name(self):
        """Test trace_mcp_list_tools with server name."""
        traced_calls = []

        def my_provider(tool_name: str, inputs):
            traced_calls.append(tool_name)
            return nullcontext()

        register_tool_trace_provider(my_provider)

        with trace_mcp_list_tools(server_name="my-server"):
            pass

        assert traced_calls == ["mcp:list_tools:my-server"]

    def test_trace_mcp_call_tool(self):
        """Test trace_mcp_call_tool context manager."""
        traced_calls = []

        def my_provider(tool_name: str, inputs):
            traced_calls.append((tool_name, inputs))
            return nullcontext()

        register_tool_trace_provider(my_provider)

        with trace_mcp_call_tool("read_file", inputs={"path": "/tmp/test.txt"}):
            pass

        assert traced_calls == [("mcp:read_file", {"path": "/tmp/test.txt"})]

    def test_trace_mcp_call_tool_with_server_name(self):
        """Test trace_mcp_call_tool with server name."""
        traced_calls = []

        def my_provider(tool_name: str, inputs):
            traced_calls.append(tool_name)
            return nullcontext()

        register_tool_trace_provider(my_provider)

        with trace_mcp_call_tool("read_file", server_name="filesystem"):
            pass

        assert traced_calls == ["mcp:filesystem:read_file"]


class TestToolTraceBuiltInProviders:
    """Tests for the built-in tool trace providers."""

    def test_weave_tool_trace_returns_nullcontext_when_not_initialized(self):
        """Test that Weave tool trace provider returns nullcontext when not initialized."""
        from openhands.sdk.observability.context import _get_weave_tool_trace

        with patch(
            "openhands.sdk.observability.weave.is_weave_initialized",
            return_value=False,
        ):
            ctx = _get_weave_tool_trace("test-tool", {"arg": "value"})
            assert type(ctx).__name__ == "nullcontext"

    def test_laminar_tool_trace_returns_nullcontext_when_not_initialized(self):
        """Test that Laminar tool trace provider returns nullcontext when not initialized."""
        from openhands.sdk.observability.context import _get_laminar_tool_trace

        with patch(
            "openhands.sdk.observability.laminar.should_enable_observability",
            return_value=False,
        ):
            ctx = _get_laminar_tool_trace("test-tool", {"arg": "value"})
            assert type(ctx).__name__ == "nullcontext"

    def test_tool_trace_providers_auto_registered(self):
        """Test that built-in tool trace providers are registered on import."""
        from openhands.sdk.observability import context

        provider_names = [p.__name__ for p in context._tool_trace_providers]
        assert "_get_weave_tool_trace" in provider_names
        assert "_get_laminar_tool_trace" in provider_names
