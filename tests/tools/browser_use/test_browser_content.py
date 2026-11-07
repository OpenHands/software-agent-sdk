"""Unit tests for CustomBrowserUseServer._get_content."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openhands.tools.browser_use.server import CustomBrowserUseServer


def _server_with_session(target_id: str | None = "target-1") -> CustomBrowserUseServer:
    server = CustomBrowserUseServer()
    session = MagicMock()
    session.agent_focus_target_id = target_id
    session.get_current_page_url = AsyncMock(return_value="http://example.test/")
    server.browser_session = session
    return server


def _stats(chars: int) -> dict:
    return {
        "original_html_chars": chars,
        "initial_markdown_chars": chars,
        "filtered_chars_removed": 0,
        "final_filtered_chars": chars,
    }


def test_get_content_reads_fresh_tree_for_focused_tab():
    server = _server_with_session()
    extract = AsyncMock(return_value=("Saved Ada", _stats(9)))

    with patch("openhands.tools.browser_use.server.extract_clean_markdown", extract):
        result = asyncio.run(server._get_content(extract_links=True))

    kwargs = extract.call_args.kwargs
    assert "browser_session" not in kwargs
    assert kwargs["target_id"] == "target-1"
    assert kwargs["dom_service"].browser_session is server.browser_session
    assert kwargs["extract_links"] is True
    assert result.startswith("<url>\nhttp://example.test/\n</url>")
    assert "<webpage_content>\nSaved Ada\n</webpage_content>" in result


def test_get_content_without_focused_tab():
    server = _server_with_session(target_id=None)
    extract = AsyncMock()

    with patch("openhands.tools.browser_use.server.extract_clean_markdown", extract):
        result = asyncio.run(server._get_content())

    assert result == "Error: No active page"
    extract.assert_not_called()
