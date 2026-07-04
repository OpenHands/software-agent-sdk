"""Test agent JSON serialization with DiscriminatedUnionMixin."""

import json
from typing import Any
from unittest.mock import Mock

import mcp.types
import pytest
from pydantic import BaseModel

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.llm import LLM
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import coerce_mcp_servers, dump_mcp_servers
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool.tool import ToolDefinition
from openhands.sdk.utils.models import OpenHandsModel


def mcp_servers_model(config: dict[str, Any]):
    return coerce_mcp_servers(config)


def dump_agent_mcp_servers(agent: AgentBase) -> dict[str, Any]:
    return dump_mcp_servers(agent.mcp_servers)


def create_mock_mcp_tool(name: str) -> MCPToolDefinition:
    # Create mock MCP tool and client
    mock_mcp_tool = mcp.types.Tool(
        name=name,
        description=f"A test MCP tool named {name}",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query parameter"}
            },
            "required": ["query"],
        },
    )
    mock_client = Mock(spec=MCPClient)
    tools = MCPToolDefinition.create(mock_mcp_tool, mock_client)
    return tools[0]  # Extract single tool from sequence


def test_agent_supports_polymorphic_json_serialization() -> None:
    """Test that Agent supports polymorphic JSON serialization/deserialization."""
    # Create a simple LLM instance and agent with empty tools
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    # Serialize to JSON (excluding non-serializable fields)
    agent_json = agent.model_dump_json()

    # Deserialize from JSON using the base class
    deserialized_agent = AgentBase.model_validate_json(agent_json)

    # Should deserialize to the correct type and have same core fields
    assert isinstance(deserialized_agent, Agent)
    assert deserialized_agent.model_dump() == agent.model_dump()


def test_mcp_tool_serialization():
    tool = create_mock_mcp_tool("test_mcp_tool_serialization")
    dumped = tool.model_dump_json()
    loaded = ToolDefinition.model_validate_json(dumped)
    assert loaded.model_dump_json() == dumped


def test_agent_serialization_redacts_mcp_servers_by_default() -> None:
    """MCP SecretStr values are redacted during default serialization."""
    llm = LLM(model="test-model", usage_id="test-llm")
    config = {
        "mcpServers": {
            "dummy": {
                "command": "echo",
                "args": ["dummy-mcp"],
                "env": {"API_KEY": "super-secret-key", "DEBUG": "true"},
                "headers": {"Authorization": "Bearer secret-token"},
            },
        }
    }
    agent = Agent(llm=llm, tools=[], mcp_servers=mcp_servers_model(config))

    # mcp_servers should be accessible in memory with full secrets
    assert dump_agent_mcp_servers(agent) == config["mcpServers"]
    assert (
        dump_agent_mcp_servers(agent)["dummy"]["env"]["API_KEY"] == "super-secret-key"
    )

    agent_dump = agent.model_dump(mode="json")
    serialized = json.dumps(agent_dump)
    assert "super-secret-key" not in serialized
    assert "secret-token" not in serialized
    server = agent_dump["mcp_servers"]["dummy"]
    assert server["env"]["API_KEY"] == "**********"
    assert server["headers"]["Authorization"] == "**********"


def test_agent_serialization_exposes_mcp_servers_with_expose_secrets() -> None:
    """Test that mcp_servers is exposed when expose_secrets=True."""
    llm = LLM(model="test-model", usage_id="test-llm")
    config = {
        "mcpServers": {
            "dummy": {
                "command": "echo",
                "args": ["dummy-mcp"],
                "env": {"API_KEY": "super-secret-key"},
            },
        }
    }
    agent = Agent(llm=llm, tools=[], mcp_servers=mcp_servers_model(config))

    # With expose_secrets=True, mcp_servers should be returned as-is
    agent_dump = agent.model_dump(mode="json", context={"expose_secrets": True})
    server = agent_dump["mcp_servers"]["dummy"]
    assert server["command"] == "echo"
    assert server["args"] == ["dummy-mcp"]
    assert server["env"]["API_KEY"] == "super-secret-key"

    # Round-trip should preserve the config
    agent_json = agent.model_dump_json(context={"expose_secrets": True})
    deserialized_agent = AgentBase.model_validate_json(agent_json)
    assert isinstance(deserialized_agent, Agent)
    assert dump_agent_mcp_servers(deserialized_agent) == config["mcpServers"]


def test_agent_serialization_encrypts_mcp_servers_with_cipher() -> None:
    """MCP SecretStr values are encrypted when cipher context is provided."""
    from openhands.sdk.utils.cipher import Cipher

    llm = LLM(model="test-model", usage_id="test-llm")
    config = {
        "mcpServers": {
            "dummy": {
                "command": "echo",
                "args": ["dummy-mcp"],
                "env": {"API_KEY": "super-secret-key"},
            },
        }
    }
    agent = Agent(llm=llm, tools=[], mcp_servers=mcp_servers_model(config))
    cipher = Cipher(secret_key="test-encryption-key")

    agent_dump = agent.model_dump(mode="json", context={"cipher": cipher})
    encrypted = agent_dump["mcp_servers"]["dummy"]["env"]["API_KEY"]
    assert encrypted != "super-secret-key"
    decrypted = cipher.decrypt(encrypted)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "super-secret-key"


def test_agent_mcp_servers_encryption_decryption_roundtrip() -> None:
    """Test full roundtrip: encrypt on serialize, decrypt on deserialize."""
    from openhands.sdk.utils.cipher import Cipher

    llm = LLM(model="test-model", usage_id="test-llm")
    config = {
        "mcpServers": {
            "fetch": {"command": "uvx", "args": ["mcp-fetch"]},
            "git": {
                "command": "uvx",
                "args": ["mcp-git", "--repo", "/tmp/test"],
                "env": {"GIT_TOKEN": "git-secret"},
            },
        }
    }
    agent = Agent(llm=llm, tools=[], mcp_servers=mcp_servers_model(config))
    cipher = Cipher(secret_key="test-encryption-key-roundtrip")

    # Serialize with cipher
    agent_json = agent.model_dump_json(context={"cipher": cipher})

    # Deserialize with same cipher
    restored_agent = AgentBase.model_validate_json(
        agent_json, context={"cipher": cipher}
    )

    # mcp_servers should be restored correctly
    assert isinstance(restored_agent, Agent)
    assert dump_agent_mcp_servers(restored_agent) == config["mcpServers"]


def test_agent_mcp_servers_accepts_plaintext_dict() -> None:
    mcp_servers = {"fetch": {"command": "uvx", "args": ["fetch"]}}
    agent_dict = {
        "llm": {"model": "test-model", "usage_id": "test-llm"},
        "tools": [],
        "mcp_servers": mcp_servers,
        "kind": "Agent",
    }

    # Deserialize - should work without cipher
    agent = AgentBase.model_validate(agent_dict)

    assert isinstance(agent, Agent)
    assert dump_agent_mcp_servers(agent) == mcp_servers


def test_agent_mcp_servers_decrypts_nested_env_and_headers_with_cipher() -> None:
    """Encrypted per-value MCP env/header settings decrypt at agent validation."""
    from pydantic import SecretStr

    from openhands.sdk.utils.cipher import Cipher

    cipher = Cipher(secret_key="test-per-value-mcp-key")
    encrypted_env = cipher.encrypt(SecretStr("ghp-plaintext-token"))
    encrypted_header = cipher.encrypt(SecretStr("Bearer plaintext-token"))
    agent_dict = {
        "llm": {"model": "test-model", "usage_id": "test-llm"},
        "tools": [],
        "mcp_servers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                    "GITHUB_PERSONAL_ACCESS_TOKEN": encrypted_env,
                    "DEBUG": "true",
                    "PORT": "1234",
                },
                "headers": {"Authorization": encrypted_header},
            }
        },
        "kind": "Agent",
    }

    agent = AgentBase.model_validate(agent_dict, context={"cipher": cipher})

    assert isinstance(agent, Agent)
    server = dump_agent_mcp_servers(agent)["github"]
    assert server["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp-plaintext-token"
    assert server["env"]["DEBUG"] == "true"
    assert server["env"]["PORT"] == "1234"
    assert server["headers"]["Authorization"] == "Bearer plaintext-token"
    assert (
        agent_dict["mcp_servers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
        == encrypted_env
    )


def test_agent_mcp_servers_rejects_non_dict_plaintext_config() -> None:
    agent_dict = {
        "llm": {"model": "test-model", "usage_id": "test-llm"},
        "tools": [],
        "mcp_servers": [],
        "kind": "Agent",
    }

    with pytest.raises(ValueError, match="Input should be a valid dictionary"):
        AgentBase.model_validate(agent_dict)


def test_agent_mcp_servers_rejects_malformed_secret_containers() -> None:
    from openhands.sdk.utils.cipher import Cipher

    cipher = Cipher(secret_key="test-per-value-mcp-key")
    agent_dict = {
        "llm": {"model": "test-model", "usage_id": "test-llm"},
        "tools": [],
        "mcp_servers": {"github": {"env": "not-a-dict"}},
        "kind": "Agent",
    }

    with pytest.raises(ValueError, match=r"mcp_servers\.github\.env"):
        AgentBase.model_validate(agent_dict, context={"cipher": cipher})


def test_agent_supports_polymorphic_field_json_serialization() -> None:
    """Test that Agent supports polymorphic JSON serialization when used as a field."""

    class Container(BaseModel):
        agent: Agent  # Use direct Agent type instead of DiscriminatedUnionType

    # Create container with agent
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    container = Container(agent=agent)

    # Serialize to JSON (excluding non-serializable fields)
    container_json = container.model_dump_json()

    # Deserialize from JSON
    deserialized_container = Container.model_validate_json(container_json)

    # Should preserve the agent type and core fields
    assert isinstance(deserialized_container.agent, Agent)
    assert deserialized_container.agent.model_dump() == agent.model_dump()


def test_agent_supports_nested_polymorphic_json_serialization() -> None:
    """Test that Agent supports nested polymorphic JSON serialization."""

    class NestedContainer(BaseModel):
        agents: list[Agent]  # Use direct Agent type

    # Create container with multiple agents
    llm1 = LLM(model="model-1", usage_id="test-llm")
    llm2 = LLM(model="model-2", usage_id="test-llm")
    agent1 = Agent(llm=llm1, tools=[])
    agent2 = Agent(llm=llm2, tools=[])
    container = NestedContainer(agents=[agent1, agent2])

    # Serialize to JSON (excluding non-serializable fields)
    container_json = container.model_dump_json()

    # Deserialize from JSON
    deserialized_container = NestedContainer.model_validate_json(container_json)

    # Should preserve all agent types and core fields
    assert len(deserialized_container.agents) == 2
    assert isinstance(deserialized_container.agents[0], Agent)
    assert isinstance(deserialized_container.agents[1], Agent)
    assert deserialized_container.agents[0].model_dump() == agent1.model_dump()
    assert deserialized_container.agents[1].model_dump() == agent2.model_dump()


def test_agent_model_validate_json_dict() -> None:
    """Test that Agent.model_validate works with dict from JSON."""
    # Create agent
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    # Serialize to JSON, then parse to dict
    agent_json = agent.model_dump_json()
    agent_dict = json.loads(agent_json)

    # Deserialize from dict
    deserialized_agent = AgentBase.model_validate(agent_dict)

    assert deserialized_agent.model_dump() == agent.model_dump()
    assert isinstance(deserialized_agent, Agent)


def test_agent_fallback_behavior_json() -> None:
    """Test that Agent handles unknown types gracefully in JSON."""
    # Create JSON with unknown kind
    agent_dict = {"llm": {"model": "test-model"}, "kind": "UnknownAgentType"}
    agent_json = json.dumps(agent_dict)

    # Should throw validation error
    with pytest.raises(ValueError):
        AgentBase.model_validate_json(agent_json)


def test_agent_preserves_pydantic_parameters_json() -> None:
    """Test that Agent preserves Pydantic parameters through JSON serialization."""
    # Create agent with extra data
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    # Serialize to JSON
    agent_json = agent.model_dump_json()

    # Deserialize from JSON
    deserialized_agent = AgentBase.model_validate_json(agent_json)

    assert deserialized_agent.model_dump() == agent.model_dump()
    assert isinstance(deserialized_agent, Agent)


def test_agent_type_annotation_works_json() -> None:
    """Test that AgentType annotation works correctly with JSON."""
    # Create agent
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    # Use AgentType annotation
    class TestModel(OpenHandsModel):
        agent: AgentBase

    model = TestModel(agent=agent)

    # Serialize to JSON
    model_json = model.model_dump_json()

    # Deserialize from JSON
    deserialized_model = TestModel.model_validate_json(model_json)

    # Should work correctly
    assert isinstance(deserialized_model.agent, Agent)
    assert deserialized_model.agent.model_dump() == agent.model_dump()
    assert deserialized_model.model_dump() == model.model_dump()


def test_agent_type_annotation_on_basemodel_works_json() -> None:
    """Test that AgentType annotation works correctly with JSON."""
    # Create agent
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    # Use AgentType annotation
    class TestModel(BaseModel):
        agent: AgentBase

    model = TestModel(agent=agent)

    # Serialize to JSON
    model_json = model.model_dump_json()

    # Deserialize from JSON
    deserialized_model = TestModel.model_validate_json(model_json)

    # Should work correctly
    assert isinstance(deserialized_model.agent, Agent)
    assert deserialized_model.agent.model_dump() == agent.model_dump()
    assert deserialized_model.model_dump() == model.model_dump()


def test_include_default_tools_serialization_default() -> None:
    """Test that include_default_tools serializes correctly with default value."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    # Serialize to JSON
    agent_json = agent.model_dump_json()
    agent_dict = json.loads(agent_json)

    # Default should include both FinishTool and ThinkTool as strings
    assert "include_default_tools" in agent_dict
    assert set(agent_dict["include_default_tools"]) == {"FinishTool", "ThinkTool"}


def test_include_default_tools_serialization_empty() -> None:
    """Test that include_default_tools serializes correctly when empty."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=[])

    # Serialize to JSON
    agent_json = agent.model_dump_json()
    agent_dict = json.loads(agent_json)

    # Should be empty list
    assert agent_dict["include_default_tools"] == []


def test_include_default_tools_serialization_partial() -> None:
    """Test that include_default_tools serializes correctly with partial list."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=["FinishTool"])

    # Serialize to JSON
    agent_json = agent.model_dump_json()
    agent_dict = json.loads(agent_json)

    # Should be serialized as string
    assert agent_dict["include_default_tools"] == ["FinishTool"]


def test_include_default_tools_deserialization_roundtrip() -> None:
    """Test that include_default_tools deserializes correctly after round-trip."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=["FinishTool"])

    # Serialize to JSON
    agent_json = agent.model_dump_json()

    # Deserialize from JSON
    deserialized_agent = AgentBase.model_validate_json(agent_json)

    # Should have the same include_default_tools
    assert isinstance(deserialized_agent, Agent)
    assert deserialized_agent.include_default_tools == ["FinishTool"]


def test_include_default_tools_deserialization_all_tools() -> None:
    """Test that include_default_tools deserializes correctly with all tools."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=["FinishTool", "ThinkTool"])

    # Serialize to JSON
    agent_json = agent.model_dump_json()

    # Deserialize from JSON
    deserialized_agent = AgentBase.model_validate_json(agent_json)

    # Should have both tools
    assert isinstance(deserialized_agent, Agent)
    assert set(deserialized_agent.include_default_tools) == {"FinishTool", "ThinkTool"}


def test_include_default_tools_deserialization_empty() -> None:
    """Test that include_default_tools deserializes correctly when empty."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=[])

    # Serialize to JSON
    agent_json = agent.model_dump_json()

    # Deserialize from JSON
    deserialized_agent = AgentBase.model_validate_json(agent_json)

    # Should be empty
    assert isinstance(deserialized_agent, Agent)
    assert deserialized_agent.include_default_tools == []


def test_include_default_tools_deserialization_from_dict() -> None:
    """Test that include_default_tools deserializes correctly from dict."""
    agent_dict = {
        "llm": {"model": "test-model", "usage_id": "test-llm"},
        "tools": [],
        "include_default_tools": ["ThinkTool"],
        "kind": "Agent",
    }

    # Deserialize from dict
    agent = AgentBase.model_validate(agent_dict)

    # Should have ThinkTool
    assert isinstance(agent, Agent)
    assert agent.include_default_tools == ["ThinkTool"]
