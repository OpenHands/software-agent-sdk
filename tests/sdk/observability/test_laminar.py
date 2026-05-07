"""Tests for Laminar observability configuration."""

import asyncio
import contextvars
import inspect
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_observability_cache():
    """Reset the module-level _observability_enabled flag between tests.

    The flag is sticky-True by design (see laminar.py docstring), so it
    leaks across tests. This fixture isolates each test from prior state.
    """
    from openhands.sdk.observability import laminar

    laminar._observability_enabled = False
    yield
    laminar._observability_enabled = False


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("https://custom.lmnr.ai", "https://custom.lmnr.ai"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("", None),
        (None, None),
    ],
)
def test_lmnr_base_url_parsing(env_value, expected):
    """Test that LMNR_BASE_URL is correctly parsed and passed to Laminar."""
    import os

    # Save original value
    original = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        # Set up environment
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if env_value is not None:
            os.environ["LMNR_BASE_URL"] = env_value
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]

        from openhands.sdk.observability.laminar import get_env

        result = get_env("LMNR_BASE_URL")
        if expected is None:
            assert result is None or result == ""
        else:
            assert result == expected
    finally:
        # Restore original values
        if original is not None:
            os.environ["LMNR_BASE_URL"] = original
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]


def test_lmnr_base_url_passed_to_laminar():
    """Test that LMNR_BASE_URL is correctly passed to Laminar.initialize."""
    import os

    # Save original values
    original_base_url = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        os.environ["LMNR_BASE_URL"] = "https://custom.lmnr.ai"

        with patch("lmnr.Laminar") as mock_laminar:
            with patch("lmnr.LaminarLiteLLMCallback"):
                with patch("litellm.callbacks", new=MagicMock()):
                    mock_laminar.is_initialized.return_value = False
                    from openhands.sdk.observability.laminar import maybe_init_laminar

                    maybe_init_laminar()

                    # Check that Laminar.initialize was called with base_url
                    call_kwargs = mock_laminar.initialize.call_args.kwargs
                    assert call_kwargs.get("base_url") == "https://custom.lmnr.ai"
    finally:
        # Restore original values
        if original_base_url is not None:
            os.environ["LMNR_BASE_URL"] = original_base_url
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]


def test_lmnr_base_url_not_passed_when_empty():
    """Test that base_url is None when LMNR_BASE_URL is not set."""
    # Save original values
    original_base_url = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]

        with patch("lmnr.Laminar") as mock_laminar:
            with patch("lmnr.LaminarLiteLLMCallback"):
                with patch("litellm.callbacks", new=MagicMock()):
                    mock_laminar.is_initialized.return_value = False
                    from openhands.sdk.observability.laminar import maybe_init_laminar

                    maybe_init_laminar()

                    # Check that Laminar.initialize was called with base_url=None
                    call_kwargs = mock_laminar.initialize.call_args.kwargs
                    assert call_kwargs.get("base_url") is None
    finally:
        # Restore original values
        if original_base_url is not None:
            os.environ["LMNR_BASE_URL"] = original_base_url
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("YES", True),
        ("on", True),
        ("ON", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_get_bool_env(env_value, expected):
    """Test that _get_bool_env correctly parses boolean environment variables."""
    original = os.environ.get("TEST_BOOL_VAR")

    try:
        if env_value is not None:
            os.environ["TEST_BOOL_VAR"] = env_value
        elif "TEST_BOOL_VAR" in os.environ:
            del os.environ["TEST_BOOL_VAR"]

        from openhands.sdk.observability.laminar import _get_bool_env

        result = _get_bool_env("TEST_BOOL_VAR")
        assert result == expected
    finally:
        if original is not None:
            os.environ["TEST_BOOL_VAR"] = original
        elif "TEST_BOOL_VAR" in os.environ:
            del os.environ["TEST_BOOL_VAR"]


def test_observe_preserves_async_signature():
    """@observe must keep an async function async so introspection works.

    Regression test for a bug where the lazy wrapper was unconditionally
    sync, causing `inspect.iscoroutinefunction` to return False for
    decorated async methods. That broke `MCPToolExecutor.__call__`, which
    relies on `iscoroutinefunction` in `run_async` to dispatch the call.
    """
    from openhands.sdk.observability.laminar import observe

    @observe(name="async_fn")
    async def async_fn(x: int) -> int:
        return x + 1

    @observe(name="sync_fn")
    def sync_fn(x: int) -> int:
        return x + 1

    assert inspect.iscoroutinefunction(async_fn)
    assert not inspect.iscoroutinefunction(sync_fn)


@pytest.mark.parametrize(
    ("force_http_value", "expected_force_http"),
    [
        ("true", True),
        ("1", True),
        ("false", False),
        ("0", False),
        (None, False),
    ],
)
def test_lmnr_force_http_passed_to_laminar(force_http_value, expected_force_http):
    """Test that LMNR_FORCE_HTTP is correctly passed to Laminar.initialize."""
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")
    original_force_http = os.environ.get("LMNR_FORCE_HTTP")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if force_http_value is not None:
            os.environ["LMNR_FORCE_HTTP"] = force_http_value
        elif "LMNR_FORCE_HTTP" in os.environ:
            del os.environ["LMNR_FORCE_HTTP"]

        with patch("lmnr.Laminar") as mock_laminar:
            with patch("lmnr.LaminarLiteLLMCallback"):
                with patch("litellm.callbacks", new=MagicMock()):
                    mock_laminar.is_initialized.return_value = False
                    from openhands.sdk.observability.laminar import maybe_init_laminar

                    maybe_init_laminar()

                    call_kwargs = mock_laminar.initialize.call_args.kwargs
                    assert call_kwargs.get("force_http") == expected_force_http
    finally:
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]
        if original_force_http is not None:
            os.environ["LMNR_FORCE_HTTP"] = original_force_http
        elif "LMNR_FORCE_HTTP" in os.environ:
            del os.environ["LMNR_FORCE_HTTP"]


# ---------------------------------------------------------------------------
# Cross-context root-span propagation
# ---------------------------------------------------------------------------
#
# Regression tests for the orphan-trace bug where ``@observe``-decorated
# methods on a Conversation, when called from a different asyncio task or
# thread than the one that constructed the Conversation, started a fresh
# trace instead of attaching to the conversation's root span. The fix moves
# from ``Laminar.start_active_span`` (which relies on contextvars
# propagation) to ``Laminar.start_span`` + ``Laminar.use_span`` re-attached
# at every entry point.


class _DummyOwner:
    """Mimics a ``BaseConversation`` for the purposes of the observe wrapper."""

    def __init__(self, root_span):
        from openhands.sdk.observability.laminar import RootSpan

        # Build a RootSpan-like object without invoking real lmnr.
        self._observability_root_span = RootSpan.__new__(RootSpan)
        self._observability_root_span.span = root_span
        self._observability_root_span._ended = False


def test_observe_calls_use_span_with_owner_root_span_on_sync():
    """Sync ``@observe``'d methods must re-attach the owner's root span."""
    os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
    try:
        from lmnr import Laminar  # noqa: F401  ensure module is importable

        from openhands.sdk.observability import laminar as lam

        sentinel_span = MagicMock(name="root-span")
        used_with: list = []

        @contextlib_compat()
        def fake_use_span(span, *args, **kwargs):
            used_with.append(span)
            yield span

        with patch.object(Laminar, "use_span", side_effect=fake_use_span):
            # Force-enable observability for the duration of this call.
            lam._observability_enabled = True
            # Stub the lmnr-level ``observe`` so the wrapper just calls through.
            with patch("lmnr.observe", lambda **kw: (lambda f: f)):

                @lam.observe(name="conversation.send_message")
                def send_message(self, msg: str) -> str:
                    return f"got {msg}"

                owner = _DummyOwner(sentinel_span)
                assert send_message(owner, "hi") == "got hi"

        assert used_with == [sentinel_span], (
            f"expected use_span to be called once with owner's root span, "
            f"got {used_with!r}"
        )
    finally:
        os.environ.pop("LMNR_PROJECT_API_KEY", None)


def test_observe_calls_use_span_with_owner_root_span_on_async():
    """Async ``@observe``'d methods must re-attach the owner's root span."""
    os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
    try:
        from lmnr import Laminar

        from openhands.sdk.observability import laminar as lam

        sentinel_span = MagicMock(name="root-span")
        used_with: list = []

        @contextlib_compat()
        def fake_use_span(span, *args, **kwargs):
            used_with.append(span)
            yield span

        with patch.object(Laminar, "use_span", side_effect=fake_use_span):
            lam._observability_enabled = True
            with patch("lmnr.observe", lambda **kw: (lambda f: f)):

                @lam.observe(name="conversation.run")
                async def run(self) -> str:
                    return "done"

                owner = _DummyOwner(sentinel_span)
                # Run from a fresh, empty contextvars Context to mimic a
                # task created outside the conversation's async ancestry.

                async def _call_in_isolated_context():
                    new_ctx = contextvars.Context()
                    return await asyncio.tasks.Task(run(owner), context=new_ctx)

                result = asyncio.run(_call_in_isolated_context())
                assert result == "done"

        assert used_with == [sentinel_span], (
            f"expected use_span to be called once even from an isolated "
            f"context, got {used_with!r}"
        )
    finally:
        os.environ.pop("LMNR_PROJECT_API_KEY", None)


def test_two_concurrent_conversations_do_not_collide():
    """Each conversation must own its own root span (no global stack).

    Before the fix, a process-wide ``SpanManager`` LIFO stack meant a second
    conversation constructed while the first was alive would corrupt the
    first's root span on close.
    """
    from openhands.sdk.conversation.base import BaseConversation

    # Bypass ABC instantiation by calling ``BaseConversation.__init__`` on a
    # bare ``object``-like instance. We only exercise the span-management
    # methods, which are concrete on the base class.
    class _BareConvo:
        pass

    c1 = _BareConvo()
    c2 = _BareConvo()
    BaseConversation.__init__(c1)  # type: ignore[arg-type]
    BaseConversation.__init__(c2)  # type: ignore[arg-type]

    # Patch the symbol in the module where it's looked up at call time, and
    # force observability on so the shortcut early-return doesn't fire.
    from openhands.sdk.conversation import base as base_mod

    with (
        patch.object(base_mod, "should_enable_observability", return_value=True),
        patch.object(
            base_mod,
            "start_root_span",
            side_effect=lambda *a, **k: MagicMock(spec_set=["end"]),
        ) as mock_start,
    ):
        BaseConversation._start_observability_span(c1, "session-1")  # type: ignore[arg-type]
        BaseConversation._start_observability_span(c2, "session-2")  # type: ignore[arg-type]

        # Each conversation has its own root span – no shared stack.
        assert c1._observability_root_span is not c2._observability_root_span  # type: ignore[attr-defined]

        # Closing c2 must NOT end c1's root span.
        c2_root = c2._observability_root_span  # type: ignore[attr-defined]
        c1_root = c1._observability_root_span  # type: ignore[attr-defined]
        BaseConversation._end_observability_span(c2)  # type: ignore[arg-type]
        c2_root.end.assert_called_once()
        c1_root.end.assert_not_called()

        # And vice versa.
        BaseConversation._end_observability_span(c1)  # type: ignore[arg-type]
        c1_root.end.assert_called_once()

        assert mock_start.call_count == 2


# Tiny shim because we want a generator-based context manager helper that
# also works as a side_effect for patch().
def contextlib_compat():
    import contextlib

    return contextlib.contextmanager
