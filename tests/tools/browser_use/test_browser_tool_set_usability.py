"""Tests for BrowserToolSet.is_usable() MCP-import probe (#5152).

Regression guard: is_usable() must return False when the browser-use MCP
server module fails to import, not just when Chromium is absent.  Without
this check the agent-server injects browser_tool_set even though create()
silently returns [] due to the mcp 2.x / browser-use 0.11.x incompatibility.
"""

import tempfile
import types
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.browser_use.definition import BrowserToolSet
from openhands.tools.browser_use.impl import BrowserToolExecutor


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Mirror of the helper in test_browser_toolset.py."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )


@pytest.fixture(autouse=True)
def reset_browser_tool_set_cache():
    """Reset BrowserToolSet class-level caches before and after each test.

    Mirrors the pattern in test_browser_toolset.py: reset both the MCP probe
    cache and the shared-executor singleton so tests are fully isolated.
    """
    BrowserToolExecutor.check_chromium_available.cache_clear()
    BrowserToolSet._mcp_import_ok = None
    BrowserToolSet._shared_executor = None
    yield
    BrowserToolExecutor.check_chromium_available.cache_clear()
    BrowserToolSet._mcp_import_ok = None
    if BrowserToolSet._shared_executor is not None:
        BrowserToolSet._shared_executor.close()
    BrowserToolSet._shared_executor = None


class TestBrowserToolSetIsUsable:
    """is_usable() must accurately reflect executor health, not just Chromium presence."""

    def test_is_usable_false_when_chromium_missing(self):
        """is_usable() returns False when Chromium is not installed."""
        with patch.object(
            BrowserToolExecutor, "check_chromium_available", return_value=None
        ):
            assert BrowserToolSet.is_usable() is False

    def test_is_usable_false_when_mcp_import_fails(self):
        """is_usable() returns False when browser-use MCP server fails to import.

        This is the #5152 regression: Chromium is present but browser-use 0.11.x
        raises AttributeError on 'list_tools' when mcp 2.x is installed.
        """
        with (
            patch.object(
                BrowserToolExecutor,
                "check_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch.object(BrowserToolSet, "_probe_mcp_import", return_value=False),
        ):
            assert BrowserToolSet.is_usable() is False

    def test_is_usable_true_when_chromium_and_mcp_ok(self):
        """is_usable() returns True when both Chromium and MCP import succeed."""
        with (
            patch.object(
                BrowserToolExecutor,
                "check_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch.object(BrowserToolSet, "_probe_mcp_import", return_value=True),
        ):
            assert BrowserToolSet.is_usable() is True


class TestProbeMcpImport:
    """_probe_mcp_import() caches the result and emits a warning on failure."""

    def test_returns_true_when_cache_is_true(self):
        """_probe_mcp_import() returns True when the cache is already set True."""
        BrowserToolSet._mcp_import_ok = True
        assert BrowserToolSet._probe_mcp_import() is True

    def test_returns_false_when_cache_holds_exception(self):
        """_probe_mcp_import() returns False when a previous probe failed."""
        BrowserToolSet._mcp_import_ok = AttributeError(
            "'Server' object has no attribute 'list_tools'"
        )
        assert BrowserToolSet._probe_mcp_import() is False

    def test_probe_succeeds_and_caches_true(self):
        """A successful import sets _mcp_import_ok to True."""
        fake_module = types.ModuleType("openhands.tools.browser_use.server")
        fake_module.CustomBrowserUseServer = object  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"openhands.tools.browser_use.server": fake_module}):
            BrowserToolSet._mcp_import_ok = None
            result = BrowserToolSet._probe_mcp_import()

        assert result is True
        assert BrowserToolSet._mcp_import_ok is True

    def test_probe_fails_with_attribute_error_and_caches_exception(self, caplog):
        """A failed import (any exception) caches the exception and emits a warning.

        The real #5152 failure mode: browser-use 0.11.x raises AttributeError at
        module import time when mcp 2.x is installed. Python's 'from X import Y'
        syntax wraps a module-level AttributeError into an ImportError, so the
        cached exception is an ImportError (or its subclass). What matters for the
        regression guard is:
          1. _probe_mcp_import() returns False.
          2. An Exception is cached (not True / None).
          3. The warning message references #5152 or 'incompatible'.
        """
        import logging

        # Simulate a module whose attribute access raises AttributeError,
        # matching browser_use/mcp/server.py under mcp 2.x. Python wraps this
        # into ImportError when 'from broken_module import CustomBrowserUseServer'
        # is executed, which is the exception the probe will cache.
        class _BrokenModule(types.ModuleType):
            def __getattr__(self, name: str) -> object:
                raise AttributeError(
                    f"'Server' object has no attribute '{name}' "
                    "(browser-use 0.11.x / mcp 2.x incompatibility)"
                )

        broken_module = _BrokenModule("openhands.tools.browser_use.server")

        BrowserToolSet._mcp_import_ok = None
        with (
            patch.dict(
                "sys.modules",
                {"openhands.tools.browser_use.server": broken_module},
            ),
            caplog.at_level(
                logging.WARNING,
                logger="openhands.tools.browser_use.definition",
            ),
        ):
            result = BrowserToolSet._probe_mcp_import()

        assert result is False
        # Any exception (ImportError wrapping AttributeError, or AttributeError
        # itself) must be cached — the probe must not swallow failures silently.
        assert isinstance(BrowserToolSet._mcp_import_ok, Exception)
        assert "5152" in caplog.text or "incompatible" in caplog.text.lower()

    def test_probe_result_is_cached(self):
        """_probe_mcp_import() never re-runs the import once a result is cached."""
        BrowserToolSet._mcp_import_ok = True
        # Call multiple times — if it re-imported it would need the real module
        for _ in range(5):
            assert BrowserToolSet._probe_mcp_import() is True


class TestBrowserToolSetCreate:
    """create() must use the MCP probe as an early guard before starting the executor."""

    def test_create_returns_empty_when_mcp_probe_fails(self):
        """create() returns [] immediately when _probe_mcp_import() returns False.

        Ensures that even if code bypasses is_usable() and calls create() directly,
        a broken MCP install never silently injects a broken tool set (#5152).
        """
        with (
            patch.object(BrowserToolSet, "_probe_mcp_import", return_value=False),
            tempfile.TemporaryDirectory() as temp_dir,
        ):
            conv_state = _create_test_conv_state(temp_dir)
            result = BrowserToolSet.create(conv_state)
        assert result == []

    def test_create_proceeds_when_mcp_probe_succeeds(self):
        """create() calls the executor when _probe_mcp_import() returns True."""
        with (
            patch.object(BrowserToolSet, "_probe_mcp_import", return_value=True),
            patch.object(
                BrowserToolSet,
                "_get_or_create_shared_executor",
                side_effect=RuntimeError("executor start failed"),
            ),
            tempfile.TemporaryDirectory() as temp_dir,
        ):
            conv_state = _create_test_conv_state(temp_dir)
            # Probe passes, but executor fails — should return [] with a warning,
            # not propagate the exception (pre-existing behaviour).
            result = BrowserToolSet.create(conv_state)
        assert result == []
