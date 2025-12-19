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
