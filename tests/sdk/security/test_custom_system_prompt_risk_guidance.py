"""Tests for restoring risk-labelling guidance when a custom system prompt is used.

Issue: https://github.com/OpenHands/software-agent-sdk/issues/5444
PR: https://github.com/OpenHands/software-agent-sdk/pull/5438
"""

from collections.abc import Sequence
from typing import Any, Self

from litellm.types.llms.openai import ChatCompletionToolParam
from pydantic import Field, SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM
from openhands.sdk.llm.message import TextContent
from openhands.sdk.security.risk import (
    CLI_TIERS,
    DEFAULT_SECURITY_RISK_DESCRIPTION,
    SANDBOX_TIERS,
)
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


class _DummyAction(Action):
    command: str = Field(default="", description="Command or query")


class _DummyObservation(Observation):
    result: str = Field(default="", description="Result of action")

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=self.result)]


class _DummyExecutor(ToolExecutor[_DummyAction, _DummyObservation]):
    def __call__(self, action: _DummyAction, conversation=None) -> _DummyObservation:
        return _DummyObservation(result=action.command)


class _MockWriteTool(ToolDefinition[_DummyAction, _DummyObservation]):
    name = "terminal"

    @classmethod
    def create(cls, conv_state=None) -> Sequence[Self]:
        return [
            cls(
                description="Run terminal commands",
                action_type=_DummyAction,
                observation_type=_DummyObservation,
                executor=_DummyExecutor(),
                annotations=ToolAnnotations(title="Terminal", readOnlyHint=False),
            )
        ]


class _MockReadTool(ToolDefinition[_DummyAction, _DummyObservation]):
    name = "reader"

    @classmethod
    def create(cls, conv_state=None) -> Sequence[Self]:
        return [
            cls(
                description="Read file contents",
                action_type=_DummyAction,
                observation_type=_DummyObservation,
                executor=_DummyExecutor(),
                annotations=ToolAnnotations(title="Reader", readOnlyHint=True),
            )
        ]


register_tool("MockWriteTool", _MockWriteTool)
register_tool("MockReadTool", _MockReadTool)


def _make_tools():
    return [Tool(name="MockWriteTool"), Tool(name="MockReadTool")]


def _make_llm():
    return LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )


def _extract_properties(schema: ChatCompletionToolParam) -> dict[str, Any]:
    fn = schema["function"]
    parameters = fn.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    props = parameters.get("properties")
    return props if isinstance(props, dict) else {}


def test_custom_system_prompt_restores_risk_guidance_cli_mode():
    """When a custom system prompt replaces the static prompt in CLI mode:
    1. Static message contains only the custom prompt.
    2. Dynamic context contains the escalation rules in <SECURITY_RISK_ASSESSMENT>.
    3. Tool schema for non-read-only tools gets the concise CLI tier definitions.
    4. Read-only tools do NOT have security_risk.
    """
    tools = _make_tools()
    agent = Agent(
        llm=_make_llm(),
        tools=tools,
        system_prompt="You are a custom security-focused coding assistant.",
        system_prompt_kwargs={"cli_mode": True},
    )
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    # 1. Static message is the custom prompt
    assert agent.static_system_message == (
        "You are a custom security-focused coding assistant."
    )
    assert "<SECURITY_RISK_ASSESSMENT>" not in agent.static_system_message

    # 2. Dynamic context has escalation rules
    dynamic = agent.dynamic_context or ""
    assert "<SECURITY_RISK_ASSESSMENT>" in dynamic
    assert "**Global Rules**" in dynamic
    assert "**Repository Context Supply Chain Rules**" in dynamic
    assert "<UNTRUSTED_CONTENT>" in dynamic
    # Dynamic context does NOT duplicate the tier definitions
    assert CLI_TIERS not in dynamic

    # 3. Tool schemas
    expected_desc = f"{DEFAULT_SECURITY_RISK_DESCRIPTION}\n\n{CLI_TIERS}"
    assert agent.security_risk_description == expected_desc
    write_tool = agent.tools_map["terminal"]
    schema = write_tool.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=agent.security_risk_description,
    )
    props = _extract_properties(schema)
    assert "security_risk" in props
    assert props["security_risk"]["description"] == expected_desc
    assert "**LOW**: Safe, read-only actions." in props["security_risk"]["description"]

    # 4. Read-only tool does not have security_risk
    read_tool = agent.tools_map["reader"]
    read_schema = read_tool.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=agent.security_risk_description,
    )
    read_props = _extract_properties(read_schema)
    assert "security_risk" not in read_props


def test_custom_system_prompt_restores_risk_guidance_sandbox_mode():
    """When a custom system prompt is used in sandbox mode (cli_mode=False):
    Tool schemas get the concise sandbox tier definitions.
    """
    tools = _make_tools()
    agent = Agent(
        llm=_make_llm(),
        tools=tools,
        system_prompt="Custom prompt.",
        system_prompt_kwargs={"cli_mode": False},
    )
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    expected_desc = f"{DEFAULT_SECURITY_RISK_DESCRIPTION}\n\n{SANDBOX_TIERS}"
    assert agent.security_risk_description == expected_desc
    write_tool = agent.tools_map["terminal"]
    schema = write_tool.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=agent.security_risk_description,
    )
    props = _extract_properties(schema)
    assert "security_risk" in props
    assert props["security_risk"]["description"] == expected_desc
    assert (
        "**LOW**: Read-only actions inside sandbox"
        in props["security_risk"]["description"]
    )


def test_default_prompt_leaves_tool_schemas_unmodified():
    """Without a custom system prompt:
    1. <SECURITY_RISK_ASSESSMENT> is in static_system_message with tiers + rules.
    2. Dynamic context does not contain <SECURITY_RISK_ASSESSMENT>.
    3. security_risk_description is None.
    4. Tool schemas do not duplicate tier definitions.
    """
    tools = _make_tools()
    agent = Agent(
        llm=_make_llm(),
        tools=tools,
    )
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    # 1. Static prompt has full risk assessment
    assert "<SECURITY_RISK_ASSESSMENT>" in agent.static_system_message
    assert CLI_TIERS in agent.static_system_message
    assert "**Global Rules**" in agent.static_system_message

    # 2. Dynamic context has no security risk section
    assert "<SECURITY_RISK_ASSESSMENT>" not in (agent.dynamic_context or "")

    # 3. Description is None
    assert agent.security_risk_description is None

    # 4. Tool schemas do NOT contain duplicated tier definitions
    write_tool = agent.tools_map["terminal"]
    schema = write_tool.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=agent.security_risk_description,
    )
    props = _extract_properties(schema)
    assert "security_risk" in props
    assert CLI_TIERS not in props["security_risk"]["description"]
    assert SANDBOX_TIERS not in props["security_risk"]["description"]


def test_analyzer_off_disables_all_risk_guidance():
    """When llm_security_analyzer is False:
    1. Neither static nor dynamic prompts have <SECURITY_RISK_ASSESSMENT>.
    2. security_risk_description is None.
    3. Tool schemas do NOT contain security_risk.
    """
    tools = _make_tools()
    # Custom prompt with analyzer False
    custom_agent = Agent(
        llm=_make_llm(),
        tools=tools,
        system_prompt="Custom prompt.",
        system_prompt_kwargs={"llm_security_analyzer": False},
    )
    convo1 = Conversation(agent=custom_agent)
    convo1._ensure_agent_ready()

    assert "<SECURITY_RISK_ASSESSMENT>" not in custom_agent.static_system_message
    assert "<SECURITY_RISK_ASSESSMENT>" not in (custom_agent.dynamic_context or "")
    assert custom_agent.security_risk_description is None
    write_tool = custom_agent.tools_map["terminal"]
    schema = write_tool.to_openai_tool(
        add_security_risk_prediction=False,
        risk_description=custom_agent.security_risk_description,
    )
    assert "security_risk" not in _extract_properties(schema)

    # Default prompt with analyzer False
    default_agent = Agent(
        llm=_make_llm(),
        tools=tools,
        system_prompt_kwargs={"llm_security_analyzer": False},
    )
    convo2 = Conversation(agent=default_agent)
    convo2._ensure_agent_ready()

    assert "<SECURITY_RISK_ASSESSMENT>" not in default_agent.static_system_message
    assert "<SECURITY_RISK_ASSESSMENT>" not in (default_agent.dynamic_context or "")
    assert default_agent.security_risk_description is None
    schema = write_tool.to_openai_tool(
        add_security_risk_prediction=False,
        risk_description=default_agent.security_risk_description,
    )
    assert "security_risk" not in _extract_properties(schema)


def test_token_measurements_for_risk_guidance():
    """Verify token overhead of the restored risk guidance."""
    # Measure default tool description
    default_desc_words = len(DEFAULT_SECURITY_RISK_DESCRIPTION.split())
    # Measure CLI tiers description
    cli_tiers_words = len(CLI_TIERS.split())
    # Measure Sandbox tiers description
    sandbox_tiers_words = len(SANDBOX_TIERS.split())

    # Default description is short (~8 words, ~13 tokens)
    assert default_desc_words < 15
    # CLI tiers is compact (< 80 words, ~85 tokens)
    assert cli_tiers_words < 80
    # Sandbox tiers is compact (< 60 words, ~65 tokens)
    assert sandbox_tiers_words < 60
