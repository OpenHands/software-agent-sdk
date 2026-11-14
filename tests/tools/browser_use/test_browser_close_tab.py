"""Unit tests for CustomBrowserUseServer._close_tab."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openhands.tools.browser_use import server as server_module
from openhands.tools.browser_use.logging_fix import LogSafeBrowserUseServer
from openhands.tools.browser_use.server import CustomBrowserUseServer


def _server(lookups: list[object | None]) -> CustomBrowserUseServer:
    server = CustomBrowserUseServer()
    session = MagicMock()
    session.get_target_id_from_tab_id = AsyncMock(return_value="TARGET-ABCD")
    session.session_manager.get_target = MagicMock(side_effect=lookups)
    server.browser_session = session
    return server


def test_close_tab_returns_once_the_target_is_gone():
    server = _server([object(), object(), None])
    closed = AsyncMock(return_value="Closed tab # ABCD, now on http://a/")

    with patch.object(LogSafeBrowserUseServer, "_close_tab", closed):
        result = asyncio.run(server._close_tab("ABCD"))

    assert result == "Closed tab # ABCD, now on http://a/"
    closed.assert_awaited_once_with("ABCD")
    manager = server.browser_session.session_manager  # type: ignore[union-attr]
    assert manager.get_target.call_count == 3
    manager.get_target.assert_called_with("TARGET-ABCD")


def test_close_tab_gives_up_after_the_settle_time(monkeypatch):
    monkeypatch.setattr(server_module, "CLOSE_TAB_SETTLE_SECONDS", 0.05)
    server = CustomBrowserUseServer()
    session = MagicMock()
    session.get_target_id_from_tab_id = AsyncMock(return_value="TARGET-ABCD")
    session.session_manager.get_target = MagicMock(return_value=object())
    server.browser_session = session
    closed = AsyncMock(return_value="Closed tab # ABCD, now on http://a/")

    with patch.object(LogSafeBrowserUseServer, "_close_tab", closed):
        result = asyncio.run(server._close_tab("ABCD"))

    assert result.startswith("Closed tab # ABCD")


def test_close_tab_without_a_session_defers_to_browser_use():
    server = CustomBrowserUseServer()
    server.browser_session = None
    closed = AsyncMock(return_value="Error: No browser session active")

    with patch.object(LogSafeBrowserUseServer, "_close_tab", closed):
        result = asyncio.run(server._close_tab("ABCD"))

    assert result == "Error: No browser session active"
