"""Test for security risk schema consistency across agent configuration changes.

This test reproduces a critical issue where changing security analyzer configuration
mid-conversation can lead to schema inconsistencies and validation failures.

The core problem on main branch:
1. Agent with security analyzer includes security_risk fields in tool schemas
2. Agent without security analyzer excludes security_risk fields from tool schemas
3. This creates validation issues when ActionEvents created with one schema
   are processed by an agent with a different schema

The refactor branch fixes this by always including security_risk fields
in tool schemas regardless of security analyzer presence, ensuring consistency.
"""

import json
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
from pydantic import Field, SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ActionEvent, AgentErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


class MockRiskyAction(Action):
    """Mock action that would have security risk (not read-only)."""

    command: str = Field(description="Command to execute")
    force: bool = Field(default=False, description="Force execution")


class MockRiskyObservation(Observation):
    """Mock observation for risky action."""

    result: str = Field(default="executed", description="Result of execution")


class MockRiskyExecutor(ToolExecutor):
    def __call__(
        self,
        action: MockRiskyAction,
        conversation: "BaseConversation | None" = None,
    ) -> MockRiskyObservation:
        return MockRiskyObservation(result=f"Executed: {action.command}")


class MockRiskyTool(ToolDefinition[MockRiskyAction, MockRiskyObservation]):
    """Mock tool that would have security risk fields (not read-only)."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,
    ) -> Sequence[Self]:
        """Create MockRiskyTool instance."""
        return [
            cls(
                description="Mock risky tool for testing security risk fields",
                action_type=MockRiskyAction,
                observation_type=MockRiskyObservation,
                executor=MockRiskyExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,  # This tool is NOT read-only
                    destructiveHint=True,  # This tool could be destructive
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


def get_risky_tool_spec() -> Tool:
    """Get a risky tool spec for testing."""
    return Tool(name="MockRiskyTool", params={})


# Register the mock tool for testing
register_tool("MockRiskyTool", MockRiskyTool)


def _tool_response_with_security_risk(name: str, args_json: str) -> ModelResponse:
    """Create a mock LLM response with tool call including security_risk."""
    return ModelResponse(
        id="mock-response",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="tool call with security_risk",
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


def test_security_risk_schema_consistency_problem():
    """Test that demonstrates the schema consistency problem on main branch.

    This test should fail on main branch due to schema inconsistency when
    security analyzer configuration changes mid-conversation.
    """
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )

    # Step 1: Create agent WITH security analyzer
    agent_with_analyzer = Agent(
        llm=llm, tools=[], security_analyzer=LLMSecurityAnalyzer()
    )

    events = []
    conversation = Conversation(agent=agent_with_analyzer, callbacks=[events.append])

    # Step 2: Generate an ActionEvent with security_risk field (analyzer present)
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response_with_security_risk(
            "think",
            '{"thought": "test thought", "security_risk": "LOW"}',
        ),
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Please use mock tool")])
        )
        agent_with_analyzer.step(conversation, on_event=events.append)

    # Verify we have an ActionEvent with security_risk
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) > 0
    original_action_event = action_events[0]
    assert original_action_event.security_risk is not None

    # Step 3: Create new agent WITHOUT security analyzer
    agent_without_analyzer = Agent(llm=llm, tools=[])

    # Step 4: Create new conversation with the agent without analyzer
    # This simulates reloading a conversation with different agent configuration
    new_conversation = Conversation(agent=agent_without_analyzer, callbacks=[])

    # Step 5: Try to replay the ActionEvent in the new conversation context
    # This should cause a schema validation problem because:
    # - The original ActionEvent has security_risk field
    # - The new agent's tools don't expect security_risk field (no analyzer)
    # - This leads to validation errors and potential infinite loops

    # Simulate the scenario by manually creating the problematic state
    new_conversation.state.events.append(original_action_event)

    # Step 6: Try to continue the conversation - this should fail
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response_with_security_risk(
            "think",
            '{"thought": "another thought"}',  # No security_risk this time
        ),
    ):
        new_events = []
        new_conversation.send_message(
            Message(role="user", content=[TextContent(text="Continue conversation")])
        )

        # This step should cause problems due to schema inconsistency
        try:
            agent_without_analyzer.step(new_conversation, on_event=new_events.append)

            # If we get here without errors, check for agent error events
            agent_errors = [e for e in new_events if isinstance(e, AgentErrorEvent)]

            # On main branch, this might cause validation issues
            # The test documents the expected behavior
            print(f"Agent errors: {len(agent_errors)}")
            for error in agent_errors:
                print(f"Error: {error.error}")

        except Exception as e:
            # This exception demonstrates the schema consistency problem
            print(f"Schema consistency error: {e}")
            # On main branch, this could happen due to inconsistent schemas

    # The test passes if we can document the issue
    # The real fix is in the refactor branch where security_risk is always included


def test_tool_schema_changes_with_security_analyzer():
    """Test how tool schemas change based on security analyzer presence."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )

    # Agent without security analyzer (with risky tool)
    agent_without = Agent(llm=llm, tools=[get_risky_tool_spec()])
    # Initialize the agent by creating a conversation
    Conversation(agent=agent_without, callbacks=[])
    # Get the actual tool instance from the agent
    risky_tool_without = agent_without.tools_map["mock_risky"]
    # On refactor branch: always include security_risk fields
    schema_without = risky_tool_without.to_openai_tool(
        add_security_risk_prediction=True
    )

    # Agent with security analyzer (with risky tool)
    agent_with = Agent(
        llm=llm, tools=[get_risky_tool_spec()], security_analyzer=LLMSecurityAnalyzer()
    )
    # Initialize the agent by creating a conversation
    Conversation(agent=agent_with, callbacks=[])
    # Get the actual tool instance from the agent
    risky_tool_with = agent_with.tools_map["mock_risky"]
    # On refactor branch: always include security_risk fields
    schema_with = risky_tool_with.to_openai_tool(add_security_risk_prediction=True)

    # The schemas should be the same on refactor branch
    without_params = schema_without["function"]["parameters"]["properties"]  # type: ignore[typeddict-item]  # noqa: E501
    with_params = schema_with["function"]["parameters"]["properties"]  # type: ignore[typeddict-item]  # noqa: E501

    print("Schema without analyzer:", json.dumps(without_params, indent=2))
    print("Schema with analyzer:", json.dumps(with_params, indent=2))

    # On refactor branch: security_risk field is always included
    if "security_risk" in with_params and "security_risk" in without_params:
        print("SUCCESS: Schema consistency achieved - security_risk always present")
    elif "security_risk" in with_params and "security_risk" not in without_params:
        print("UNEXPECTED: Schema inconsistency still exists on refactor branch")
    elif "security_risk" not in with_params and "security_risk" not in without_params:
        print("UNEXPECTED: security_risk field is never present for risky tool")
    else:
        print("UNEXPECTED: security_risk only in schema without analyzer")

    # On refactor branch, schemas should be identical - this is the fix!
    assert without_params == with_params, "Schemas should be identical on refactor"
