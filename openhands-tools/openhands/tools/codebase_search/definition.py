"""Codebase search tool registration backed by @morphllm/morphmcp.

This module registers ``codebase_search`` and ``github_codebase_search`` as
native OpenHands tools.  Under the hood they are MCP tools served by the
``@morphllm/morphmcp`` npm package (started via ``npx``).

The ``edit_file`` tool exposed by the same MCP server is intentionally
filtered out to avoid conflicts with the built-in ``FileEditorTool``.

.. note::
    Unlike most tools that subclass :class:`ToolDefinition` and implement
    ``create()``, these tools use **callable resolver functions** registered
    directly with :func:`register_tool`.  The registry invokes them as
    ``factory(conv_state=conv_state, **params)``; see
    :func:`_resolver_from_callable` in ``openhands.sdk.tool.registry``.
"""

from __future__ import annotations

import atexit
import os
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.utils import create_mcp_tools
from openhands.sdk.tool import ToolDefinition, register_tool

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)

# ── Shared MCP client cache ────────────────────────────────────────────
# Both resolvers share a single MCP server process per API key so that
# agents using both tools don't spawn two ``npx`` processes.
_lock = threading.Lock()
_morph_clients: dict[tuple[str, str | None, int | None], tuple[object, list[ToolDefinition]]] = {}

_SEARCH_TOOL_NAMES = frozenset({"codebase_search", "github_codebase_search"})


def _validate_api_key(params: dict) -> str:
    """Return a validated MORPH_API_KEY or raise with a helpful message."""
    api_key: str | None = params.get("api_key") or os.environ.get("MORPH_API_KEY")
    if not api_key:
        raise ValueError(
            "MORPH_API_KEY is required for codebase_search.\n"
            "Set it as an environment variable:\n"
            "  export MORPH_API_KEY=sk-morph-...\n"
            "Or pass it in Tool params:\n"
            "  Tool(name='codebase_search', params={'api_key': 'sk-morph-...'})\n\n"
            "Get your key at https://morphllm.com/dashboard/api-keys"
        )
    return api_key


def _get_morph_search_tools(
    api_key: str,
    api_url: str | None = None,
    timeout_ms: int | None = None,
) -> list[ToolDefinition]:
    """Start (or reuse) the Morph MCP server and return search tools only."""
    cache_key = (api_key, api_url, timeout_ms)

    with _lock:
        if cache_key in _morph_clients:
            _, tools = _morph_clients[cache_key]
            return tools

        # Build environment for the MCP server process
        env: dict[str, str] = {"MORPH_API_KEY": api_key}
        if api_url or os.environ.get("MORPH_API_URL"):
            env["MORPH_API_URL"] = api_url or os.environ["MORPH_API_URL"]
        if timeout_ms or os.environ.get("MORPH_WARP_GREP_TIMEOUT"):
            env["MORPH_WARP_GREP_TIMEOUT"] = str(
                timeout_ms or os.environ["MORPH_WARP_GREP_TIMEOUT"]
            )

        mcp_config = {
            "mcpServers": {
                "morph": {
                    "command": "npx",
                    "args": ["-y", "@morphllm/morphmcp"],
                    "env": env,
                }
            }
        }

        # Timeout for the MCP handshake (seconds).  The WarpGrep model timeout
        # (MORPH_WARP_GREP_TIMEOUT) is separate and controls per-search duration
        # inside the MCP server.
        handshake_timeout = 60.0

        client = create_mcp_tools(mcp_config, timeout=handshake_timeout)
        search_tools: list[ToolDefinition] = [
            t for t in client if t.name in _SEARCH_TOOL_NAMES
        ]

        logger.info(
            "Morph MCP server started — exposing tools: %s",
            [t.name for t in search_tools],
        )

        _morph_clients[cache_key] = (client, search_tools)
        return search_tools


# ── MCP client cleanup ─────────────────────────────────────────────────

def _cleanup_morph_clients() -> None:
    """Close all cached MCP clients on process exit."""
    with _lock:
        for client, _ in _morph_clients.values():
            try:
                client.sync_close()
            except Exception:
                pass
        _morph_clients.clear()


atexit.register(_cleanup_morph_clients)


# ── Resolvers ───────────────────────────────────────────────────────────

def _codebase_search_resolver(
    conv_state: "ConversationState | None" = None,  # noqa: ARG001
    **params: Any,
) -> Sequence[ToolDefinition]:
    api_key = _validate_api_key(params)
    tools = _get_morph_search_tools(
        api_key=api_key,
        api_url=params.get("api_url"),
        timeout_ms=params.get("timeout"),
    )
    return [t for t in tools if t.name == "codebase_search"]


def _github_codebase_search_resolver(
    conv_state: "ConversationState | None" = None,  # noqa: ARG001
    **params: Any,
) -> Sequence[ToolDefinition]:
    api_key = _validate_api_key(params)
    tools = _get_morph_search_tools(
        api_key=api_key,
        api_url=params.get("api_url"),
        timeout_ms=params.get("timeout"),
    )
    return [t for t in tools if t.name == "github_codebase_search"]


# ── Public registration ─────────────────────────────────────────────────

def register_codebase_search_tools() -> None:
    """Register ``codebase_search`` and ``github_codebase_search`` tools.

    Call this once before creating an Agent that uses these tools.
    Registration is explicit (not at import time) to avoid starting MCP
    server processes when the module is merely imported.
    """
    register_tool("codebase_search", _codebase_search_resolver)
    register_tool("github_codebase_search", _github_codebase_search_resolver)
