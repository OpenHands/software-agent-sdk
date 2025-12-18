"""Tests for Weave observability integration.

These tests verify the Weave integration works correctly, including:
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

    def test_weave_op_preserves_function_metadata(self):
        """@weave_op preserves function name and docstring."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import weave_op

        @weave_op(name="custom_name")
        def my_function(x: int) -> int:
            """My docstring."""
            return x

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

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


class TestObserveWeaveDecorator:
    """Tests for the @observe_weave decorator."""

    def test_observe_weave_without_initialization(self):
        """@observe_weave runs function normally when Weave is not initialized."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import observe_weave

        @observe_weave(name="test_observe")
        def test_function(x: int, y: int) -> int:
            return x + y

        result = test_function(3, 4)
        assert result == 7

    def test_observe_weave_with_ignore_inputs(self):
        """@observe_weave correctly handles ignore_inputs parameter."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import observe_weave

        @observe_weave(name="test_ignore", ignore_inputs=["secret"])
        def test_function(data: str, secret: str) -> str:
            return f"{data}-processed"

        result = test_function("hello", "my-secret")
        assert result == "hello-processed"


class TestWeaveThread:
    """Tests for the weave_thread context manager."""

    def test_weave_thread_without_initialization(self):
        """weave_thread works as no-op when Weave is not initialized."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import weave_thread

        results = []
        with weave_thread("test-thread-123"):
            results.append(1)
            results.append(2)

        assert results == [1, 2]


class TestWeaveSpanManager:
    """Tests for the WeaveSpanManager class."""

    def test_span_manager_without_initialization(self):
        """WeaveSpanManager works gracefully when Weave is not initialized."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import WeaveSpanManager

        manager = WeaveSpanManager()

        # start_span should return None when not initialized
        result = manager.start_span("test_span", inputs={"key": "value"})
        assert result is None

        # end_span should not raise
        manager.end_span(output={"result": "ok"})

    def test_global_span_functions(self):
        """Global span functions work without initialization."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = False

        from openhands.sdk.observability.weave import (
            start_weave_span,
            end_weave_span,
        )

        # Should not raise
        result = start_weave_span("test", inputs={"x": 1})
        assert result is None

        # Should not raise
        end_weave_span(output={"y": 2})


class TestWeaveExports:
    """Tests for module exports."""

    def test_all_exports_available(self):
        """All expected functions are exported from the module."""
        from openhands.sdk.observability import (
            end_weave_span,
            get_weave_client,
            init_weave,
            is_weave_initialized,
            maybe_init_weave,
            observe_weave,
            should_enable_weave,
            start_weave_span,
            weave_op,
            weave_thread,
            WeaveSpanManager,
        )

        # Just verify they're callable
        assert callable(end_weave_span)
        assert callable(get_weave_client)
        assert callable(init_weave)
        assert callable(is_weave_initialized)
        assert callable(maybe_init_weave)
        assert callable(observe_weave)
        assert callable(should_enable_weave)
        assert callable(start_weave_span)
        assert callable(weave_op)
        assert callable(weave_thread)
        assert WeaveSpanManager is not None


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
                mock_weave_init.assert_called_once_with("test-project")

    def test_init_weave_already_initialized(self):
        """init_weave returns True immediately if already initialized."""
        import openhands.sdk.observability.weave as weave_module
        weave_module._weave_initialized = True

        from openhands.sdk.observability.weave import init_weave

        result = init_weave(project="test")
        assert result is True

        # Reset for other tests
        weave_module._weave_initialized = False
