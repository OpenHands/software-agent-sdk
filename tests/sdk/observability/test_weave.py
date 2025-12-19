"""Tests for Weave observability integration.

These tests verify the Weave integration works correctly, including:
- Automatic LLM tracing via Weave's autopatching
- Decorator functionality (with and without Weave initialized)
- Environment variable configuration
- Graceful fallback when Weave is not available
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestWeaveConfiguration:
    """Tests for Weave configuration and initialization."""

    def test_should_enable_weave_with_both_vars(self):
        """should_enable_weave returns True when both env vars are set."""
        from openhands.sdk.observability.weave import should_enable_weave

        with patch.dict(os.environ, {
            "WANDB_API_KEY": "test-key",
            "WEAVE_PROJECT": "test-project",
        }):
            assert should_enable_weave() is True

    def test_should_enable_weave_missing_api_key(self):
        """should_enable_weave returns False when API key is missing."""
        from openhands.sdk.observability.weave import should_enable_weave

        with patch.dict(os.environ, {
            "WEAVE_PROJECT": "test-project",
        }, clear=True):
            # Clear WANDB_API_KEY if it exists
            os.environ.pop("WANDB_API_KEY", None)
            assert should_enable_weave() is False

    def test_should_enable_weave_missing_project(self):
        """should_enable_weave returns False when project is missing."""
        from openhands.sdk.observability.weave import should_enable_weave

        with patch.dict(os.environ, {
            "WANDB_API_KEY": "test-key",
        }, clear=True):
            os.environ.pop("WEAVE_PROJECT", None)
            assert should_enable_weave() is False

    def test_is_weave_initialized_default(self):
        """is_weave_initialized returns False by default."""
        # Reset global state
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import is_weave_initialized
        assert is_weave_initialized() is False


class TestWeaveOpDecorator:
    """Tests for the @weave_op decorator."""

    def test_weave_op_without_initialization(self):
        """@weave_op runs function normally when Weave is not initialized."""
        # Reset global state
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import weave_op

        @weave_op(name="test_function")
        def test_function(x: int) -> int:
            return x + 1

        result = test_function(5)
        assert result == 6

    def test_weave_op_without_parentheses(self):
        """@weave_op can be used without parentheses."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import weave_op

        @weave_op
        def test_function(x: int) -> int:
            return x + 1

        result = test_function(5)
        assert result == 6

    def test_weave_op_handles_exceptions(self):
        """@weave_op propagates exceptions correctly."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import weave_op

        @weave_op(name="failing_function")
        def failing_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_function()


class TestGetWeaveOp:
    """Tests for the get_weave_op function."""

    def test_get_weave_op_returns_noop_when_not_initialized(self):
        """get_weave_op returns a no-op decorator when Weave is not initialized."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import get_weave_op

        op = get_weave_op()

        @op
        def test_function(x: int) -> int:
            return x * 2

        # Function should work normally
        assert test_function(5) == 10
        # Function should be unchanged
        assert test_function.__name__ == "test_function"


class TestWeaveExports:
    """Tests for module exports."""

    def test_all_exports_available(self):
        """All expected functions are exported from the module."""
        from openhands.sdk.observability import (
            get_weave_client,
            get_weave_op,
            init_weave,
            is_weave_initialized,
            maybe_init_weave,
            should_enable_weave,
            weave_op,
        )

        # Just verify they're callable
        assert callable(get_weave_client)
        assert callable(get_weave_op)
        assert callable(init_weave)
        assert callable(is_weave_initialized)
        assert callable(maybe_init_weave)
        assert callable(should_enable_weave)
        assert callable(weave_op)


class TestInitWeave:
    """Tests for init_weave function."""

    def test_init_weave_requires_project(self):
        """init_weave raises ValueError when no project is specified."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import init_weave

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("WEAVE_PROJECT", None)
            with pytest.raises(ValueError, match="Weave project must be specified"):
                init_weave()

    def test_init_weave_uses_env_project(self):
        """init_weave uses WEAVE_PROJECT from environment."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import init_weave

        # Mock weave.init to avoid actual initialization
        with patch("openhands.sdk.observability.weave.get_env") as mock_get_env:
            mock_get_env.side_effect = lambda k: {
                "WEAVE_PROJECT": "test-project",
                "WANDB_API_KEY": None,
            }.get(k)

            with patch("weave.init") as mock_weave_init:
                mock_weave_init.return_value = MagicMock()
                result = init_weave()

                # Should have called weave.init with the project
                mock_weave_init.assert_called_once()

    def test_init_weave_already_initialized(self):
        """init_weave returns True immediately if already initialized."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = True

        from openhands.sdk.observability.weave import init_weave

        result = init_weave(project="test")
        assert result is True

        # Reset for other tests
        weave_module._weave_initialized = False


class TestAutopatching:
    """Tests for Weave's autopatching behavior.

    These tests verify that the integration is designed to leverage
    Weave's automatic LiteLLM patching.
    """

    def test_init_weave_calls_weave_init(self):
        """init_weave calls weave.init which triggers autopatching."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import init_weave

        with patch("openhands.sdk.observability.weave.get_env") as mock_get_env:
            mock_get_env.side_effect = lambda k: {
                "WEAVE_PROJECT": "test-project",
                "WANDB_API_KEY": "test-key",
            }.get(k)

            with patch("weave.init") as mock_weave_init:
                with patch("wandb.login"):
                    mock_weave_init.return_value = MagicMock()
                    result = init_weave()

                    # weave.init should be called, which triggers implicit_patch()
                    # and register_import_hook() internally
                    mock_weave_init.assert_called_once()
                    assert result is True

        # Reset for other tests
        weave_module._weave_initialized = False
