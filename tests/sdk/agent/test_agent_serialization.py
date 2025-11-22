"""Test agent JSON serialization with DiscriminatedUnionMixin."""

import json
from typing import cast
from unittest.mock import Mock

import mcp.types
import pytest
from pydantic import BaseModel

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.llm import LLM
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool.tool import ToolDefinition
from openhands.sdk.utils.models import OpenHandsModel


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


def test_agent_serialization_should_include_mcp_tool() -> None:
    # Create a simple LLM instance and agent with empty tools
    llm = LLM(model="test-model", usage_id="test-llm")
    mcp_config = {
        "mcpServers": {
            "dummy": {"command": "echo", "args": ["dummy-mcp"]},
        }
    }
    agent = Agent(llm=llm, tools=[], mcp_config=cast(dict[str, object], mcp_config))

    # Serialize to JSON (excluding non-serializable fields)
    agent_dump = agent.model_dump()
    assert agent_dump.get("mcp_config") == mcp_config
    agent_json = agent.model_dump_json()

    # Deserialize from JSON using the base class
    deserialized_agent = AgentBase.model_validate_json(agent_json)

    # Should deserialize to the correct type and have same core fields
    assert isinstance(deserialized_agent, Agent)
    assert deserialized_agent.model_dump_json() == agent.model_dump_json()


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


def test_agent_system_message_selects_gpt5_prompt_when_available(tmp_path, monkeypatch):
    """AgentBase.system_message should switch to system_prompt-gpt.j2 for GPT-5.

    The switch only occurs when:
    - system_prompt_filename is left as the default "system_prompt.j2"; and
    - the LLM model is GPT-5 family; and
    - a sibling system_prompt-gpt.j2 file exists in the prompts dir.
    """

    from openhands.sdk.agent import Agent

    # Create a temp prompts directory with both default and GPT-5 templates
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    default_template = prompts_dir / "system_prompt.j2"
    gpt5_template = prompts_dir / "system_prompt-gpt.j2"

    default_template.write_text("DEFAULT_PROMPT", encoding="utf-8")
    gpt5_template.write_text("GPT5_PROMPT", encoding="utf-8")

    # Monkeypatch Agent.prompt_dir to point at our temp prompts_dir.
    # We avoid subclassing Agent here, since AgentBase discriminated unions
    # require globally unique "kind" values per concrete subclass.
    def _prompt_dir(self) -> str:  # type: ignore[override]
        return str(prompts_dir)

    monkeypatch.setattr(Agent, "prompt_dir", property(_prompt_dir))

    llm = LLM(model="gpt-5-mini", usage_id="test-gpt5-agent")
    agent = Agent(llm=llm, tools=[])

    # With GPT-5 model and both templates present, we should pick GPT5_PROMPT
    assert agent.system_message == "GPT5_PROMPT"


def test_agent_system_message_uses_default_prompt_when_not_gpt5(tmp_path, monkeypatch):
    """Non-GPT-5 models should continue to use the default system prompt."""

    from openhands.sdk.agent import Agent

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    default_template = prompts_dir / "system_prompt.j2"
    gpt5_template = prompts_dir / "system_prompt-gpt.j2"

    default_template.write_text("DEFAULT_PROMPT", encoding="utf-8")
    gpt5_template.write_text("GPT5_PROMPT", encoding="utf-8")

    def _prompt_dir(self) -> str:  # type: ignore[override]
        return str(prompts_dir)

    monkeypatch.setattr(Agent, "prompt_dir", property(_prompt_dir))

    llm = LLM(model="gpt-4o", usage_id="test-gpt4-agent")
    agent = Agent(llm=llm, tools=[])

    # Non-GPT-5 model should still render from system_prompt.j2
    assert agent.system_message == "DEFAULT_PROMPT"


def test_agent_system_message_uses_configured_prompt_filename_even_for_gpt5(
    tmp_path, monkeypatch
):
    """If system_prompt_filename is overridden, GPT-5 should not auto-switch."""

    from openhands.sdk.agent import Agent

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    custom_template = prompts_dir / "custom_prompt.j2"
    gpt5_template = prompts_dir / "system_prompt-gpt.j2"

    custom_template.write_text("CUSTOM_PROMPT", encoding="utf-8")
    gpt5_template.write_text("GPT5_PROMPT", encoding="utf-8")

    def _prompt_dir(self) -> str:  # type: ignore[override]
        return str(prompts_dir)

    monkeypatch.setattr(Agent, "prompt_dir", property(_prompt_dir))

    llm = LLM(model="gpt-5-mini", usage_id="test-gpt5-custom-prompt")
    agent = Agent(
        llm=llm,
        tools=[],
        system_prompt_filename="custom_prompt.j2",
    )

    # Because system_prompt_filename was overridden, we should respect it and not
    # auto-swap to the GPT-5 template.
    assert agent.system_message == "CUSTOM_PROMPT"


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
