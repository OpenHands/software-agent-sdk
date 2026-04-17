"""Test that mcp_config secrets are sanitized during serialization.

MCP config can contain secrets in server headers (e.g., Authorization tokens),
environment variables, and API keys. The field_serializer on AgentBase.mcp_config
must redact these before they reach base_state.json, ConversationStateUpdateEvents,
or cloud storage.
"""

import json
import tempfile
import uuid
from pathlib import Path

from fastmcp.mcp_config import MCPConfig, RemoteMCPServer
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def _make_agent(
    mcp_config: dict | MCPConfig | None = None,
) -> Agent:
    """Helper to create an Agent with default LLM."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    cfg: MCPConfig | None = None
    if isinstance(mcp_config, dict):
        cfg = MCPConfig.model_validate(mcp_config) if mcp_config else None
    else:
        cfg = mcp_config
    return Agent(llm=llm, tools=[], mcp_config=cfg)


# -- Tests for MCPConfig type coercion --


def test_mcp_config_dict_coerced_to_mcpconfig():
    """Passing a raw dict is coerced to MCPConfig via the field validator."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
        }
    )
    assert isinstance(agent.mcp_config, MCPConfig)
    assert "fetch" in agent.mcp_config.mcpServers


def test_mcp_config_none_stays_none():
    """None mcp_config remains None."""
    agent = _make_agent(mcp_config=None)
    assert agent.mcp_config is None


def test_mcp_config_empty_dict_normalised_to_none():
    """Empty dict is normalised to None."""
    agent = _make_agent(mcp_config={})
    assert agent.mcp_config is None


# -- Tests for model_dump / model_dump_json redaction --


def test_mcp_config_headers_redacted_on_dump():
    """Authorization headers in shttp server config are redacted."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "slack": {
                    "url": "https://mcp.example.com/slack",
                    "headers": {
                        "Authorization": "Bearer sk-secret-token-123",
                        "Content-Type": "application/json",
                    },
                }
            }
        }
    )

    dumped = agent.model_dump(mode="json")
    headers = dumped["mcp_config"]["mcpServers"]["slack"]["headers"]
    assert headers["Authorization"] == "<redacted>"
    assert headers["Content-Type"] == "<redacted>"  # all header values redacted


def test_mcp_config_env_vars_redacted_on_dump():
    """Environment variables in stdio server config are redacted."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "tavily": {
                    "command": "uvx",
                    "args": ["mcp-server-tavily"],
                    "env": {
                        "TAVILY_API_KEY": "tvly-secret-key-abc123",
                        "HOME": "/home/user",
                    },
                }
            }
        }
    )

    dumped = agent.model_dump(mode="json")
    env = dumped["mcp_config"]["mcpServers"]["tavily"]["env"]
    assert env["TAVILY_API_KEY"] == "<redacted>"
    assert env["HOME"] == "<redacted>"  # all env values redacted


def test_mcp_config_api_key_field_redacted_on_dump():
    """Top-level api_key fields are redacted by is_secret_key matching."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "custom": {
                    "url": "https://api.example.com",
                    "api_key": "sk-secret-value",
                }
            }
        }
    )

    dumped = agent.model_dump(mode="json")
    assert dumped["mcp_config"]["mcpServers"]["custom"]["api_key"] == "<redacted>"
    # Non-secret fields preserved
    assert (
        dumped["mcp_config"]["mcpServers"]["custom"]["url"] == "https://api.example.com"
    )


def test_mcp_config_empty_serialises_to_empty_dict():
    """None mcp_config serialises as empty dict for backward compat."""
    agent = _make_agent(mcp_config={})
    dumped = agent.model_dump(mode="json")
    assert dumped["mcp_config"] == {}


def test_mcp_config_no_secrets_preserved():
    """mcp_config without secrets preserves non-sensitive fields."""
    config = {
        "mcpServers": {
            "fetch": {
                "command": "uvx",
                "args": ["mcp-server-fetch"],
            }
        }
    }
    agent = _make_agent(mcp_config=config)
    dumped = agent.model_dump(mode="json")
    server = dumped["mcp_config"]["mcpServers"]["fetch"]
    assert server["command"] == "uvx"
    assert server["args"] == ["mcp-server-fetch"]


def test_mcp_config_model_dump_json_redacts():
    """model_dump_json (used by _save_base_state) also redacts secrets."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "slack": {
                    "url": "https://mcp.example.com",
                    "headers": {"Authorization": "Bearer real-token"},
                    "env": {"SECRET_KEY": "my-secret"},
                }
            }
        }
    )

    json_str = agent.model_dump_json()
    parsed = json.loads(json_str)
    server = parsed["mcp_config"]["mcpServers"]["slack"]
    assert server["headers"]["Authorization"] == "<redacted>"
    assert server["env"]["SECRET_KEY"] == "<redacted>"
    assert server["url"] == "https://mcp.example.com"


def test_mcp_config_roundtrip_preserves_structure():
    """Serialization→deserialization preserves structure with redacted values."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "shttp_server": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                },
                "stdio_server": {
                    "command": "npx",
                    "args": ["-y", "server"],
                    "env": {"API_TOKEN": "tok_abc"},
                },
            }
        }
    )

    json_str = agent.model_dump_json()
    restored = AgentBase.model_validate_json(json_str)

    # Structure preserved, secrets redacted; restored as MCPConfig
    assert isinstance(restored.mcp_config, MCPConfig)
    assert "shttp_server" in restored.mcp_config.mcpServers
    assert "stdio_server" in restored.mcp_config.mcpServers


# -- Tests for ConversationStateUpdateEvent pathway --


def test_state_update_event_redacts_agent_mcp_config():
    """ConversationStateUpdateEvent with key='agent' redacts mcp_config."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "slack": {
                    "url": "https://mcp.example.com",
                    "headers": {"Authorization": "Bearer real-secret"},
                }
            }
        }
    )

    # Simulate the __setattr__ path
    event = ConversationStateUpdateEvent(key="agent", value=agent)
    event_data = event.model_dump(mode="json")

    # The agent in the value should have mcp_config sanitized
    agent_value = event_data["value"]
    headers = agent_value["mcp_config"]["mcpServers"]["slack"]["headers"]
    assert headers["Authorization"] == "<redacted>"


def test_full_state_snapshot_redacts_mcp_config():
    """ConversationStateUpdateEvent.from_conversation_state redacts mcp_config."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "tavily": {
                    "command": "uvx",
                    "args": ["mcp-server-tavily"],
                    "env": {"TAVILY_API_KEY": "tvly-real-secret"},
                }
            }
        }
    )

    state = ConversationState.create(
        agent=agent,
        id=uuid.UUID("12345678-1234-5678-9abc-123456789001"),
        workspace=LocalWorkspace(working_dir="/tmp"),
    )

    event = ConversationStateUpdateEvent.from_conversation_state(state)
    assert event.key == "full_state"

    agent_data = event.value["agent"]
    env = agent_data["mcp_config"]["mcpServers"]["tavily"]["env"]
    assert env["TAVILY_API_KEY"] == "<redacted>"
    # Non-sensitive fields preserved
    assert agent_data["mcp_config"]["mcpServers"]["tavily"]["command"] == "uvx"


# -- Tests for persistence (base_state.json) pathway --


def test_persisted_base_state_has_redacted_mcp_config():
    """base_state.json written to disk does not contain MCP secrets."""
    with tempfile.TemporaryDirectory() as temp_dir:
        agent = _make_agent(
            mcp_config={
                "mcpServers": {
                    "slack": {
                        "url": "https://mcp.example.com/slack",
                        "headers": {"Authorization": "Bearer real-token-value"},
                        "env": {"SLACK_TOKEN": "xoxb-real-slack-token"},
                    }
                }
            }
        )

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789099")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        # Force save
        state._save_base_state(state._fs)

        # Read the persisted file directly
        base_state_path = Path(persist_path) / "base_state.json"
        base_state = json.loads(base_state_path.read_text())

        agent_data = base_state["agent"]
        server = agent_data["mcp_config"]["mcpServers"]["slack"]

        # Secrets must be redacted
        assert server["headers"]["Authorization"] == "<redacted>"
        assert server["env"]["SLACK_TOKEN"] == "<redacted>"

        # Non-sensitive fields preserved
        assert server["url"] == "https://mcp.example.com/slack"

        # Also verify the raw file doesn't contain the actual secret values
        raw_text = base_state_path.read_text()
        assert "real-token-value" not in raw_text
        assert "xoxb-real-slack-token" not in raw_text


def test_mcp_config_runtime_value_unaffected():
    """The in-memory MCPConfig is NOT modified by serialization.

    field_serializer only affects the output of model_dump/model_dump_json,
    not the actual field value on the instance.
    """
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "slack": {
                    "url": "https://mcp.example.com",
                    "headers": {"Authorization": "Bearer real-token"},
                }
            }
        }
    )

    # Serialize (triggers field_serializer)
    _ = agent.model_dump(mode="json")

    # In-memory MCPConfig is untouched
    assert isinstance(agent.mcp_config, MCPConfig)
    slack = agent.mcp_config.mcpServers["slack"]
    assert isinstance(slack, RemoteMCPServer)
    assert slack.headers["Authorization"] == "Bearer real-token"


def test_multiple_servers_all_sanitized():
    """All servers in mcp_config have their secrets redacted."""
    agent = _make_agent(
        mcp_config={
            "mcpServers": {
                "server_a": {
                    "url": "https://a.example.com",
                    "headers": {"Authorization": "Bearer token-a"},
                },
                "server_b": {
                    "url": "https://b.example.com",
                    "headers": {"X-API-Key": "key-b"},
                    "env": {"SECRET_VAR": "secret-val"},
                },
                "server_c": {
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"DB_PASSWORD": "p@ssw0rd"},
                },
            }
        }
    )

    dumped = agent.model_dump(mode="json")
    servers = dumped["mcp_config"]["mcpServers"]

    assert servers["server_a"]["headers"]["Authorization"] == "<redacted>"
    assert servers["server_b"]["headers"]["X-API-Key"] == "<redacted>"
    assert servers["server_b"]["env"]["SECRET_VAR"] == "<redacted>"
    assert servers["server_c"]["env"]["DB_PASSWORD"] == "<redacted>"

    # Non-sensitive fields preserved
    assert servers["server_a"]["url"] == "https://a.example.com"
    assert servers["server_c"]["command"] == "node"
    assert servers["server_c"]["args"] == ["server.js"]
