"""Test security_risk field validation in agent tool calls."""

from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ActionEvent, AgentErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer


def _tool_response(name: str, args_json: str) -> ModelResponse:
    """Create a mock LLM response with a tool call."""
    return ModelResponse(
        id="mock-response",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="tool call response",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call_1",
                            type="function",
                            function=Function(name=name, arguments=args_json),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def test_security_risk_field_always_included_in_tool_schema():
    """Test that security_risk field is always included in tool schemas."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )

    # Test with no security analyzer
    agent_no_analyzer = Agent(
        llm=llm, tools=[]
    )  # Built-in tools are added automatically

    # Test with LLM security analyzer
    agent_with_analyzer = Agent(
        llm=llm,
        tools=[],  # Built-in tools are added automatically
        security_analyzer=LLMSecurityAnalyzer(),
    )

    # Initialize agents to load tools
    import tempfile
    import uuid

    from openhands.sdk.conversation import ConversationState
    from openhands.sdk.io import InMemoryFileStore
    from openhands.sdk.workspace import LocalWorkspace

    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace = LocalWorkspace(working_dir=tmp_dir)
        state = ConversationState(
            id=uuid.uuid4(),
            workspace=workspace,
            persistence_dir=f"{tmp_dir}/.state",
            agent=agent_no_analyzer,
        )
        state._fs = InMemoryFileStore()
        state._autosave_enabled = False

        agent_no_analyzer._initialize(state)
        agent_with_analyzer._initialize(state)

        # Both should include security_risk field in tool schemas
        # Get the actual tool definition from the agent
        think_tool = agent_no_analyzer._tools["think"]

        # Check OpenAI tool format
        openai_tool_no_analyzer = think_tool.to_openai_tool(
            add_security_risk_prediction=agent_no_analyzer._add_security_risk_prediction
        )
        openai_tool_with_analyzer = think_tool.to_openai_tool(
            add_security_risk_prediction=agent_with_analyzer._add_security_risk_prediction
        )

        # Both should include security_risk field
        openai_func_no_analyzer = openai_tool_no_analyzer["function"]
        openai_func_with_analyzer = openai_tool_with_analyzer["function"]
        assert openai_func_no_analyzer.get("parameters") is not None
        assert openai_func_with_analyzer.get("parameters") is not None
        assert (
            "security_risk" in openai_func_no_analyzer["parameters"]["properties"]  # type: ignore[index]
        )
        assert (
            "security_risk" in openai_func_with_analyzer["parameters"]["properties"]  # type: ignore[index]
        )

        # Check responses tool format
        responses_tool_no_analyzer = think_tool.to_responses_tool(
            add_security_risk_prediction=agent_no_analyzer._add_security_risk_prediction
        )
        responses_tool_with_analyzer = think_tool.to_responses_tool(
            add_security_risk_prediction=agent_with_analyzer._add_security_risk_prediction
        )

        # Both should include security_risk field
        assert responses_tool_no_analyzer.get("parameters") is not None
        assert responses_tool_with_analyzer.get("parameters") is not None
        assert (
            "security_risk" in responses_tool_no_analyzer["parameters"]["properties"]  # type: ignore[index]
        )
        assert (
            "security_risk" in responses_tool_with_analyzer["parameters"]["properties"]  # type: ignore[index]
        )


def test_llm_security_analyzer_requires_security_risk_field():
    """Test that LLMSecurityAnalyzer requires security_risk field in LLM response."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[], security_analyzer=LLMSecurityAnalyzer())

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response without security_risk field
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought"}',  # Missing security_risk
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # Should have an agent error due to missing security_risk
    agent_errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(agent_errors) == 1
    assert "security_risk field is missing" in agent_errors[0].error
    assert "LLMSecurityAnalyzer is configured" in agent_errors[0].error


def test_llm_security_analyzer_validates_security_risk_values():
    """Test that LLMSecurityAnalyzer validates security_risk values."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[], security_analyzer=LLMSecurityAnalyzer())

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response with invalid security_risk value
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought", "security_risk": "INVALID"}',
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # Should have an agent error due to invalid security_risk value
    agent_errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(agent_errors) == 1
    assert "Invalid security_risk value from LLM: INVALID" in agent_errors[0].error
    assert "Expected one of:" in agent_errors[0].error


def test_llm_security_analyzer_accepts_valid_security_risk():
    """Test that LLMSecurityAnalyzer accepts valid security_risk values."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[], security_analyzer=LLMSecurityAnalyzer())

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response with valid security_risk value
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought", "security_risk": "LOW"}',
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # Should not have any agent errors
    agent_errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(agent_errors) == 0

    # Should have a successful ActionEvent with the correct security_risk
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1
    assert action_events[0].security_risk.value == "LOW"


def test_non_llm_security_analyzer_handles_missing_security_risk():
    """Test that non-LLM security analyzers handle missing security_risk gracefully."""
    from openhands.sdk.security.analyzer import SecurityAnalyzerBase
    from openhands.sdk.security.risk import SecurityRisk

    class MockSecurityAnalyzer(SecurityAnalyzerBase):
        def security_risk(self, action: ActionEvent) -> SecurityRisk:
            return SecurityRisk.MEDIUM

    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[], security_analyzer=MockSecurityAnalyzer())

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response without security_risk field
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought"}',  # Missing security_risk
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # Should not have any agent errors (non-LLM analyzers don't require the field)
    agent_errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(agent_errors) == 0

    # Should have a successful ActionEvent with default security_risk
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1
    assert action_events[0].security_risk.value == "UNKNOWN"  # Default value


def test_no_security_analyzer_handles_missing_security_risk():
    """Test that agents without security analyzers handle missing security_risk gracefully."""  # noqa: E501
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[])  # No security analyzer

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response without security_risk field
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought"}',  # Missing security_risk
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # Should not have any agent errors
    agent_errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(agent_errors) == 0

    # Should have a successful ActionEvent with default security_risk
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1
    assert action_events[0].security_risk.value == "UNKNOWN"  # Default value


@pytest.mark.parametrize("risk_value", ["LOW", "MEDIUM", "HIGH", "UNKNOWN"])
def test_llm_security_analyzer_accepts_all_valid_risk_values(risk_value: str):
    """Test that LLMSecurityAnalyzer accepts all valid SecurityRisk enum values."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[], security_analyzer=LLMSecurityAnalyzer())

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response with the given security_risk value
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            f'{{"thought": "This is a test thought", "security_risk": "{risk_value}"}}',
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # Should not have any agent errors
    agent_errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(agent_errors) == 0

    # Should have a successful ActionEvent with the correct security_risk
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1
    assert action_events[0].security_risk.value == risk_value
