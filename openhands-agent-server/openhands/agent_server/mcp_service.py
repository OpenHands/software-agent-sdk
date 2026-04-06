"""Service for managing server-level MCP server configurations.

MCP servers registered here are:
- Persisted to disk so they survive server restarts
- Validated on registration (config is checked, tools are discovered)
- Available to conversations by referencing their human-readable IDs
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp.mcp_config import MCPConfig

from openhands.agent_server.models import (
    MCPServerInfo,
    MCPServerStatus,
    MCPServerToolInfo,
)


logger = logging.getLogger(__name__)


@dataclass
class _StoredMCPServer:
    """Internal representation of a registered MCP server."""

    id: str
    config: dict[str, Any]
    status: MCPServerStatus
    tools: list[MCPServerToolInfo]
    error: str | None
    created_at: datetime
    updated_at: datetime

    def to_info(self) -> MCPServerInfo:
        return MCPServerInfo(
            id=self.id,
            config=self.config,
            status=self.status,
            tools=self.tools,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


def _validate_mcp_config(config: dict[str, Any]) -> MCPConfig:
    """Validate and parse an MCP config dict.

    Raises:
        ValueError: If the config is invalid.
    """
    if "mcpServers" not in config:
        raise ValueError(
            "MCP config must contain 'mcpServers' key. "
            "Example: {'mcpServers': {'fetch': {'command': 'uvx', "
            "'args': ['mcp-server-fetch']}}}"
        )
    if not config["mcpServers"]:
        raise ValueError("'mcpServers' must contain at least one server definition")
    return MCPConfig.model_validate(config)


def _discover_tools(
    config: dict[str, Any], timeout: float = 30.0
) -> list[MCPServerToolInfo]:
    """Connect to the MCP server(s), list tools, then disconnect.

    This validates that the server is reachable and discovers available tools.
    """
    from openhands.sdk.mcp import create_mcp_tools

    client = create_mcp_tools(config, timeout=timeout)
    try:
        return [
            MCPServerToolInfo(name=tool.name, description=tool.description)
            for tool in client.tools
        ]
    finally:
        client.sync_close()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class MCPService:
    """Manages server-level MCP server configurations.

    Configs are persisted as individual JSON files in ``storage_dir``.
    On startup, all persisted configs are loaded and validated.
    """

    storage_dir: Path
    _servers: dict[str, _StoredMCPServer] = field(default_factory=dict)

    # ── Lifecycle ──

    async def start(self) -> None:
        """Load persisted MCP server configs and validate them."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        for config_file in sorted(self.storage_dir.glob("*.json")):
            try:
                data = json.loads(config_file.read_text())
                mcp_id = data["id"]
                config = data["config"]
                created_at = datetime.fromisoformat(data["created_at"])

                # Validate config structure
                _validate_mcp_config(config)

                # Try to discover tools (validates connectivity)
                try:
                    tools = _discover_tools(config)
                    status = MCPServerStatus.ACTIVE
                    error = None
                    logger.info(
                        "MCP server '%s' started with %d tool(s): %s",
                        mcp_id,
                        len(tools),
                        [t.name for t in tools],
                    )
                except Exception as e:
                    tools = []
                    status = MCPServerStatus.ERROR
                    error = str(e)
                    logger.warning("MCP server '%s' failed to start: %s", mcp_id, error)

                self._servers[mcp_id] = _StoredMCPServer(
                    id=mcp_id,
                    config=config,
                    status=status,
                    tools=tools,
                    error=error,
                    created_at=created_at,
                    updated_at=_utc_now(),
                )
            except Exception:
                logger.exception("Failed to load MCP config from %s", config_file)

        logger.info("MCP service started: %d server(s) loaded", len(self._servers))

    async def stop(self) -> None:
        """Clean up on shutdown."""
        self._servers.clear()
        logger.info("MCP service stopped")

    # ── CRUD ──

    def create_server(self, mcp_id: str, config: dict[str, Any]) -> MCPServerInfo:
        """Register and validate a new MCP server configuration.

        Raises:
            ValueError: If the ID already exists or config is invalid.
        """
        if mcp_id in self._servers:
            raise ValueError(f"MCP server '{mcp_id}' already exists")

        _validate_mcp_config(config)

        # Discover tools to validate the server works
        try:
            tools = _discover_tools(config)
            status = MCPServerStatus.ACTIVE
            error = None
        except Exception as e:
            tools = []
            status = MCPServerStatus.ERROR
            error = str(e)
            logger.warning("MCP server '%s' validation failed: %s", mcp_id, error)

        now = _utc_now()
        server = _StoredMCPServer(
            id=mcp_id,
            config=config,
            status=status,
            tools=tools,
            error=error,
            created_at=now,
            updated_at=now,
        )
        self._servers[mcp_id] = server
        self._persist(server)

        logger.info(
            "MCP server '%s' registered (%s, %d tools)",
            mcp_id,
            status.value,
            len(tools),
        )
        return server.to_info()

    def get_server(self, mcp_id: str) -> MCPServerInfo | None:
        """Get info about a registered MCP server."""
        server = self._servers.get(mcp_id)
        return server.to_info() if server else None

    def list_servers(self) -> list[MCPServerInfo]:
        """List all registered MCP servers."""
        return [s.to_info() for s in self._servers.values()]

    def update_server(self, mcp_id: str, config: dict[str, Any]) -> MCPServerInfo:
        """Update an existing MCP server's configuration.

        Raises:
            KeyError: If the server doesn't exist.
            ValueError: If the new config is invalid.
        """
        if mcp_id not in self._servers:
            raise KeyError(f"MCP server '{mcp_id}' not found")

        existing = self._servers[mcp_id]
        _validate_mcp_config(config)

        # Re-discover tools with new config
        try:
            tools = _discover_tools(config)
            status = MCPServerStatus.ACTIVE
            error = None
        except Exception as e:
            tools = []
            status = MCPServerStatus.ERROR
            error = str(e)
            logger.warning(
                "MCP server '%s' validation failed after update: %s", mcp_id, error
            )

        server = _StoredMCPServer(
            id=mcp_id,
            config=config,
            status=status,
            tools=tools,
            error=error,
            created_at=existing.created_at,
            updated_at=_utc_now(),
        )
        self._servers[mcp_id] = server
        self._persist(server)

        logger.info(
            "MCP server '%s' updated (%s, %d tools)",
            mcp_id,
            status.value,
            len(tools),
        )
        return server.to_info()

    def delete_server(self, mcp_id: str) -> bool:
        """Delete a registered MCP server.

        Returns True if deleted, False if not found.
        """
        server = self._servers.pop(mcp_id, None)
        if server is None:
            return False

        config_file = self.storage_dir / f"{mcp_id}.json"
        if config_file.exists():
            config_file.unlink()

        logger.info("MCP server '%s' deleted", mcp_id)
        return True

    def get_config_for_ids(self, mcp_server_ids: list[str]) -> dict[str, Any]:
        """Build a merged MCP config dict for the given server IDs.

        Returns a config with all servers' ``mcpServers`` entries merged.

        Raises:
            KeyError: If any ID is not found.
            ValueError: If any referenced server is in error state.
        """
        merged: dict[str, Any] = {}

        for mcp_id in mcp_server_ids:
            server = self._servers.get(mcp_id)
            if server is None:
                raise KeyError(f"MCP server '{mcp_id}' not found")
            if server.status == MCPServerStatus.ERROR:
                raise ValueError(
                    f"MCP server '{mcp_id}' is in error state: {server.error}"
                )
            server_entries = server.config.get("mcpServers", {})
            merged.update(server_entries)

        if not merged:
            return {}
        return {"mcpServers": merged}

    # ── Persistence ──

    def _persist(self, server: _StoredMCPServer) -> None:
        """Write a single server config to disk."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        config_file = self.storage_dir / f"{server.id}.json"
        data = {
            "id": server.id,
            "config": server.config,
            "created_at": server.created_at.isoformat(),
        }
        config_file.write_text(json.dumps(data, indent=2))


# ── Singleton ──

_mcp_service: MCPService | None = None


def get_mcp_service() -> MCPService:
    """Get the global MCP service instance."""
    global _mcp_service
    if _mcp_service is None:
        from openhands.agent_server.config import get_default_config

        config = get_default_config()
        _mcp_service = MCPService(
            storage_dir=config.conversations_path.parent / "mcp_servers"
        )
    return _mcp_service
