"""Test that tool validation error messages are concise and don't include values."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

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
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent, ObservationEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class ValidationTestAction(Action):
    """Action for validation testing."""

    command: str = ""
    path: str = ""
    old_str: str = ""


class ValidationTestObservation(Observation):
    """Observation for validation testing."""

    result: str = ""


class ValidationTestExecutor(
    ToolExecutor[ValidationTestAction, ValidationTestObservation]
):
    """Executor that just returns an observation."""

    def __call__(
        self, action: ValidationTestAction, conversation=None
    ) -> ValidationTestObservation:
        return ValidationTestObservation(result="ok")


class ValidationTestTool(
    ToolDefinition[ValidationTestAction, ValidationTestObservation]
):
    """Tool for testing validation error messages."""

    name = "validation_test_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="A tool for testing validation errors",
                action_type=ValidationTestAction,
                observation_type=ValidationTestObservation,
                executor=ValidationTestExecutor(),
            )
        ]


register_tool("ValidationTestTool", ValidationTestTool)


def test_validation_error_shows_keys_not_values():
    """Error message should show parameter keys, not large argument values."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="ValidationTestTool")])

    # Create tool call with large arguments and an invalid security_risk to
    # trigger a validation error in the same code path.
    large_value = "x" * 1000
    tool_args = (
        f'{{"command": "view", "path": "/test", "old_str": "{large_value}", '
        f'"security_risk": "INVALID"}}'
    )

    def mock_llm_response(messages, **kwargs):
        return ModelResponse(
            id="mock-1",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="I'll use the tool.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(
                                    name="validation_test_tool", arguments=tool_args
                                ),
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

    collected_events = []
    conversation = Conversation(agent=agent, callbacks=[collected_events.append])
    conversation.set_security_analyzer(LLMSecurityAnalyzer())

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", side_effect=mock_llm_response
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Do something")])
        )
        agent.step(conversation, on_event=collected_events.append)

    error_events = [e for e in collected_events if isinstance(e, AgentErrorEvent)]
    assert len(error_events) == 1

    error_msg = error_events[0].error
    # Error should include tool name and parameter keys
    assert "validation_test_tool" in error_msg
    assert "Parameters provided:" in error_msg
    assert "command" in error_msg
    assert "path" in error_msg
    assert "old_str" in error_msg
    # Error should NOT include the large value (1000 x's)
    assert large_value not in error_msg


def test_unparseable_json_error_message():
    """Error message should indicate unparseable JSON when parsing fails."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="ValidationTestTool")])

    # Invalid JSON that cannot be parsed
    invalid_json = "{invalid json syntax"

    def mock_llm_response(messages, **kwargs):
        return ModelResponse(
            id="mock-1",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="I'll use the tool.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(
                                    name="validation_test_tool", arguments=invalid_json
                                ),
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

    collected_events = []
    conversation = Conversation(agent=agent, callbacks=[collected_events.append])

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", side_effect=mock_llm_response
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Do something")])
        )
        agent.step(conversation, on_event=collected_events.append)

    error_events = [e for e in collected_events if isinstance(e, AgentErrorEvent)]
    assert len(error_events) == 1

    error_msg = error_events[0].error
    assert "validation_test_tool" in error_msg
    assert "unparseable JSON" in error_msg


def _mock_llm_response_factory(tool_args: str):
    """Return a mock LLM callable that emits one tool call with the given args."""

    def mock_llm_response(messages, **kwargs):
        return ModelResponse(
            id="mock-1",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="I'll use the tool.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(
                                    name="validation_test_tool",
                                    arguments=tool_args,
                                ),
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

    return mock_llm_response


def test_tool_call_without_security_risk_succeeds():
    """Omitting security_risk should not raise; the action gets UNKNOWN risk."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="ValidationTestTool")])

    # Two valid args, NO security_risk field
    tool_args = '{"command": "view", "path": "/test"}'

    collected_events = []
    conversation = Conversation(agent=agent, callbacks=[collected_events.append])
    conversation.set_security_analyzer(LLMSecurityAnalyzer())

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=_mock_llm_response_factory(tool_args),
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Do something")])
        )
        agent.step(conversation, on_event=collected_events.append)

    # No error events should be emitted
    error_events = [e for e in collected_events if isinstance(e, AgentErrorEvent)]
    assert error_events == [], (
        f"Expected no errors when security_risk is omitted, got: {error_events}"
    )

    # An ActionEvent with UNKNOWN risk should have been emitted
    action_events = [e for e in collected_events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1
    assert action_events[0].security_risk == SecurityRisk.UNKNOWN


def test_omitted_security_risk_still_requires_confirmation():
    """With LLMSecurityAnalyzer + ConfirmRisky, UNKNOWN risk must not auto-proceed."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="ValidationTestTool")])

    # Two valid args, NO security_risk field
    tool_args = '{"command": "view", "path": "/test"}'

    collected_events = []
    conversation = Conversation(agent=agent, callbacks=[collected_events.append])
    conversation.set_security_analyzer(LLMSecurityAnalyzer())
    # confirm_unknown defaults to True, so the default ConfirmRisky policy
    # will require confirmation for UNKNOWN-risk actions.
    conversation.set_confirmation_policy(ConfirmRisky())

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=_mock_llm_response_factory(tool_args),
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Do something")])
        )
        agent.step(conversation, on_event=collected_events.append)

    # The action should be pending confirmation, not auto-executed
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    # An ActionEvent should exist with UNKNOWN risk
    action_events = [e for e in collected_events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1
    assert action_events[0].security_risk == SecurityRisk.UNKNOWN

    # No observation should have been produced (action was not executed)
    observation_events = [
        e for e in collected_events if isinstance(e, ObservationEvent)
    ]
    assert observation_events == [], (
        "Action should not have been executed while waiting for confirmation"
    )
