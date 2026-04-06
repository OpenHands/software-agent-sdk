"""Tests for MCP service."""

import json
from unittest.mock import patch

import pytest

from openhands.agent_server.mcp_service import (
    MCPService,
    _validate_mcp_config,
)
from openhands.agent_server.models import MCPServerStatus, MCPServerToolInfo


@pytest.fixture
def storage_dir(tmp_path):
    return tmp_path / "mcp_servers"


@pytest.fixture
def mcp_service(storage_dir):
    return MCPService(storage_dir=storage_dir)


VALID_CONFIG = {
    "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
}

VALID_CONFIG_TWO_SERVERS = {
    "mcpServers": {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
        "time": {"command": "uvx", "args": ["mcp-server-time"]},
    }
}

FAKE_TOOLS = [
    MCPServerToolInfo(name="fetch", description="Fetch a URL"),
]


class TestValidateMCPConfig:
    def test_valid_config(self):
        _validate_mcp_config(VALID_CONFIG)

    def test_missing_mcp_servers_key(self):
        with pytest.raises(ValueError, match="mcpServers"):
            _validate_mcp_config({"foo": "bar"})

    def test_empty_mcp_servers(self):
        with pytest.raises(ValueError, match="at least one"):
            _validate_mcp_config({"mcpServers": {}})


class TestMCPServiceCRUD:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_create_server(self, mock_discover, mcp_service, storage_dir):
        info = mcp_service.create_server("test-server", VALID_CONFIG)

        assert info.id == "test-server"
        assert info.status == MCPServerStatus.ACTIVE
        assert len(info.tools) == 1
        assert info.tools[0].name == "fetch"
        assert info.error is None

        # Check persistence
        config_file = storage_dir / "test-server.json"
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["id"] == "test-server"
        assert data["config"] == VALID_CONFIG

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_create_duplicate_server(self, mock_discover, mcp_service):
        mcp_service.create_server("test-server", VALID_CONFIG)
        with pytest.raises(ValueError, match="already exists"):
            mcp_service.create_server("test-server", VALID_CONFIG)

    def test_create_server_invalid_config(self, mcp_service):
        with pytest.raises(ValueError, match="mcpServers"):
            mcp_service.create_server("bad", {"foo": "bar"})

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        side_effect=RuntimeError("connection failed"),
    )
    def test_create_server_discovery_failure(self, mock_discover, mcp_service):
        info = mcp_service.create_server("fail-server", VALID_CONFIG)
        assert info.status == MCPServerStatus.ERROR
        assert "connection failed" in info.error
        assert info.tools == []

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_get_server(self, mock_discover, mcp_service):
        mcp_service.create_server("test-server", VALID_CONFIG)
        info = mcp_service.get_server("test-server")
        assert info is not None
        assert info.id == "test-server"

    def test_get_nonexistent_server(self, mcp_service):
        assert mcp_service.get_server("nonexistent") is None

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_list_servers(self, mock_discover, mcp_service):
        mcp_service.create_server("server-a", VALID_CONFIG)
        mcp_service.create_server("server-b", VALID_CONFIG)
        servers = mcp_service.list_servers()
        assert len(servers) == 2
        ids = {s.id for s in servers}
        assert ids == {"server-a", "server-b"}

    def test_list_empty(self, mcp_service):
        assert mcp_service.list_servers() == []

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_update_server(self, mock_discover, mcp_service):
        mcp_service.create_server("test-server", VALID_CONFIG)

        new_config = {
            "mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}
        }
        info = mcp_service.update_server("test-server", new_config)
        assert info.config == new_config
        assert info.status == MCPServerStatus.ACTIVE

    def test_update_nonexistent_server(self, mcp_service):
        with pytest.raises(KeyError, match="not found"):
            mcp_service.update_server("nonexistent", VALID_CONFIG)

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_delete_server(self, mock_discover, mcp_service, storage_dir):
        mcp_service.create_server("test-server", VALID_CONFIG)
        assert (storage_dir / "test-server.json").exists()

        assert mcp_service.delete_server("test-server") is True
        assert mcp_service.get_server("test-server") is None
        assert not (storage_dir / "test-server.json").exists()

    def test_delete_nonexistent_server(self, mcp_service):
        assert mcp_service.delete_server("nonexistent") is False


class TestMCPServiceGetConfigForIds:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_get_config_for_single_id(self, mock_discover, mcp_service):
        mcp_service.create_server("test-server", VALID_CONFIG)
        config = mcp_service.get_config_for_ids(["test-server"])
        assert "mcpServers" in config
        assert "fetch" in config["mcpServers"]

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_get_config_for_multiple_ids(self, mock_discover, mcp_service):
        config_a = {
            "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
        }
        config_b = {
            "mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}
        }
        mcp_service.create_server("server-a", config_a)
        mcp_service.create_server("server-b", config_b)

        config = mcp_service.get_config_for_ids(["server-a", "server-b"])
        assert "fetch" in config["mcpServers"]
        assert "time" in config["mcpServers"]

    def test_get_config_for_unknown_id(self, mcp_service):
        with pytest.raises(KeyError, match="not found"):
            mcp_service.get_config_for_ids(["nonexistent"])

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        side_effect=RuntimeError("boom"),
    )
    def test_get_config_for_error_server(self, mock_discover, mcp_service):
        mcp_service.create_server("broken", VALID_CONFIG)
        with pytest.raises(ValueError, match="error state"):
            mcp_service.get_config_for_ids(["broken"])

    def test_get_config_for_empty_list(self, mcp_service):
        assert mcp_service.get_config_for_ids([]) == {}


class TestMCPServicePersistence:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    @pytest.mark.asyncio
    async def test_load_on_startup(self, mock_discover, storage_dir):
        # Create a service and add a server
        service1 = MCPService(storage_dir=storage_dir)
        service1.create_server("persist-test", VALID_CONFIG)

        # Create a new service instance and start it (simulates restart)
        service2 = MCPService(storage_dir=storage_dir)
        await service2.start()

        info = service2.get_server("persist-test")
        assert info is not None
        assert info.id == "persist-test"
        assert info.status == MCPServerStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_start_empty_dir(self, mcp_service):
        await mcp_service.start()
        assert mcp_service.list_servers() == []

    @pytest.mark.asyncio
    async def test_stop_clears_servers(self, mcp_service):
        with patch(
            "openhands.agent_server.mcp_service._discover_tools",
            return_value=FAKE_TOOLS,
        ):
            mcp_service.create_server("test", VALID_CONFIG)
        await mcp_service.stop()
        assert mcp_service.list_servers() == []

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    @pytest.mark.asyncio
    async def test_corrupt_file_skipped(self, mock_discover, storage_dir):
        # Write a corrupt file
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "bad.json").write_text("not json")

        # Write a valid file
        valid_data = {
            "id": "good",
            "config": VALID_CONFIG,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        (storage_dir / "good.json").write_text(json.dumps(valid_data))

        service = MCPService(storage_dir=storage_dir)
        await service.start()

        # Only the valid one should load
        assert len(service.list_servers()) == 1
        assert service.get_server("good") is not None
