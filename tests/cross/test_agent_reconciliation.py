"""Test agent reconciliation logic in agent deserialization and conversation restart."""

import tempfile
import uuid
from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.agent import AgentBase
from openhands.sdk.context import AgentContext, Skill
from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.preset.default import get_default_agent
from openhands.tools.terminal import TerminalTool


register_tool("TerminalTool", TerminalTool)
register_tool("FileEditorTool", FileEditorTool)


# Tests from test_llm_reconciliation.py
def test_conversation_restart_with_nested_llms(tmp_path):
    """Test conversation restart with agent containing nested LLMs."""
    # Create a default agent with dummy LLM + models + keys

    working_dir = str(tmp_path)

    llm = LLM(
        model="gpt-4o-mini", api_key=SecretStr("llm-api-key"), usage_id="main-llm"
    )

    # Use the standard Agent class to avoid polymorphic deserialization issues
    agent = get_default_agent(llm)

    conversation_id = uuid.uuid4()

    # Create a conversation with the default agent + persistence
    conversation1 = Conversation(
        agent=agent,
        persistence_dir=working_dir,
        conversation_id=conversation_id,
    )

    # Verify the conversation was created successfully
    assert conversation1.id == conversation_id
    assert conversation1.agent.llm.api_key is not None
    assert isinstance(conversation1.agent.llm.api_key, SecretStr)
    assert conversation1.agent.llm.api_key.get_secret_value() == "llm-api-key"
    assert isinstance(conversation1.agent.condenser, LLMSummarizingCondenser)
    assert conversation1.agent.condenser.llm.api_key is not None
    assert isinstance(conversation1.agent.condenser.llm.api_key, SecretStr)
    assert conversation1.agent.condenser.llm.api_key.get_secret_value() == "llm-api-key"

    # Attempt to restart the conversation - this should work without errors
    conversation2 = Conversation(
        agent=agent,
        persistence_dir=working_dir,
        conversation_id=conversation_id,  # Same conversation_id
    )

    # Make sure the conversation gets initialized properly with no errors
    assert conversation2.id == conversation_id
    assert conversation2.agent.llm.api_key is not None
    assert isinstance(conversation2.agent.llm.api_key, SecretStr)
    assert conversation2.agent.llm.api_key.get_secret_value() == "llm-api-key"
    assert isinstance(conversation2.agent.condenser, LLMSummarizingCondenser)
    assert conversation2.agent.condenser.llm.api_key is not None
    assert isinstance(conversation2.agent.condenser.llm.api_key, SecretStr)
    assert conversation2.agent.condenser.llm.api_key.get_secret_value() == "llm-api-key"

    # Verify that the agent configuration is properly reconciled
    assert conversation2.agent.llm.model == "gpt-4o-mini"
    assert conversation2.agent.condenser.llm.model == "gpt-4o-mini"
    assert conversation2.agent.condenser.max_size == 80
    assert conversation2.agent.condenser.keep_first == 4


def test_conversation_restarted_with_changed_working_directory(tmp_path_factory):
    working_dir = str(tmp_path_factory.mktemp("persist"))

    llm = LLM(
        model="gpt-4o-mini", api_key=SecretStr("llm-api-key"), usage_id="main-llm"
    )

    agent1 = get_default_agent(llm)
    conversation_id = uuid.uuid4()

    # first conversation
    _ = Conversation(
        agent=agent1, persistence_dir=working_dir, conversation_id=conversation_id
    )

    # agent built in a *different* temp dir
    agent2 = get_default_agent(llm)

    # restart with new agent working dir but same conversation id
    _ = Conversation(
        agent=agent2, persistence_dir=working_dir, conversation_id=conversation_id
    )


# Tests from test_local_conversation_tools_integration.py
def test_conversation_allows_removing_unused_tools():
    """Test that removing tools that weren't used in history is allowed.

    With relaxed tool matching, only tools that were actually USED in the
    conversation history need to be present. Unused tools can be removed.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation with original agent having 2 tools
        original_tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        original_agent = Agent(llm=llm, tools=original_tools)
        conversation = LocalConversation(
            agent=original_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            visualizer=None,
        )

        # Send a message but NO tool is used (no ActionEvent in history)
        conversation.send_message(
            Message(role="user", content=[TextContent(text="test message")])
        )

        conversation_id = conversation.state.id
        del conversation

        # Resume with only one tool - should succeed since no tools were used
        reduced_tools = [Tool(name="TerminalTool")]  # Removed FileEditorTool
        llm2 = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        reduced_agent = Agent(llm=llm2, tools=reduced_tools)

        # This should succeed - FileEditorTool was never used
        new_conversation = LocalConversation(
            agent=reduced_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            conversation_id=conversation_id,
            visualizer=None,
        )
        assert new_conversation.id == conversation_id


def test_conversation_allows_adding_new_tools():
    """Test that adding new tools not in the original conversation is allowed.

    With relaxed tool matching, new tools can be added when resuming a
    conversation without causing failures.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation with only one tool
        original_tools = [Tool(name="TerminalTool")]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        original_agent = Agent(llm=llm, tools=original_tools)
        conversation = LocalConversation(
            agent=original_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            visualizer=None,
        )

        # Send a message (no tools used)
        conversation.send_message(
            Message(role="user", content=[TextContent(text="test message")])
        )

        conversation_id = conversation.state.id
        del conversation

        # Resume with additional tools - should succeed
        expanded_tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),  # New tool added
        ]
        llm2 = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        expanded_agent = Agent(llm=llm2, tools=expanded_tools)

        # This should succeed - adding new tools is allowed
        new_conversation = LocalConversation(
            agent=expanded_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            conversation_id=conversation_id,
            visualizer=None,
        )
        assert new_conversation.id == conversation_id
        # Verify the new tools are available
        assert len(new_conversation.agent.tools) == 2


def test_conversation_fails_when_used_tool_is_missing():
    """Test that removing a tool that WAS used in history fails.

    This is the core correctness requirement: if a tool was actually used
    in the conversation history, it MUST be present when resuming.
    """
    from openhands.sdk.event import ActionEvent

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation with two tools
        original_tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        original_agent = Agent(llm=llm, tools=original_tools)
        conversation = LocalConversation(
            agent=original_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            visualizer=None,
        )

        # Initialize the agent to get actual tool definitions
        conversation.agent.init_state(conversation.state, lambda e: None)

        # Simulate that TerminalTool was used by adding an ActionEvent
        from openhands.sdk.llm import MessageToolCall, TextContent

        action_event = ActionEvent(
            tool_name="TerminalTool",
            tool_call_id="test-call-1",
            thought=[TextContent(text="Running a command")],
            tool_call=MessageToolCall(
                id="test-call-1",
                name="TerminalTool",
                arguments="{}",
                origin="completion",
            ),
            llm_response_id="test-response-1",
        )
        conversation.state.events.append(action_event)

        conversation_id = conversation.state.id
        del conversation

        # Try to resume WITHOUT TerminalTool - should fail
        reduced_tools = [Tool(name="FileEditorTool")]  # Missing TerminalTool
        llm2 = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        reduced_agent = Agent(llm=llm2, tools=reduced_tools)

        # This should raise - TerminalTool was used in history but is now missing
        import pytest

        with pytest.raises(ValueError, match="tools that were used in history"):
            LocalConversation(
                agent=reduced_agent,
                workspace=temp_dir,
                persistence_dir=temp_dir,
                conversation_id=conversation_id,
                visualizer=None,
            )


def test_conversation_with_same_agent_succeeds():
    """Test that using the same agent configuration succeeds."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create and save conversation
        tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        original_agent = Agent(llm=llm, tools=tools)
        conversation = LocalConversation(
            agent=original_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            visualizer=None,
        )

        # Send a message
        conversation.send_message(
            Message(role="user", content=[TextContent(text="test message")])
        )

        # Get the conversation ID for reuse
        conversation_id = conversation.state.id

        # Delete conversation
        del conversation

        # Create new conversation with same agent configuration
        same_tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]
        llm2 = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        same_agent = Agent(llm=llm2, tools=same_tools)

        # This should succeed
        new_conversation = LocalConversation(
            agent=same_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            conversation_id=conversation_id,  # Use same ID
            visualizer=None,
        )

        # Verify state was loaded
        assert len(new_conversation.state.events) > 0


def test_agent_resolve_diff_from_deserialized():
    """Test agent's resolve_diff_from_deserialized method.

    Includes tolerance for litellm_extra_body differences injected at CLI load time.
    """
    with tempfile.TemporaryDirectory():
        # Create original agent
        tools = [Tool(name="TerminalTool")]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        original_agent = Agent(llm=llm, tools=tools)

        # Serialize and deserialize to simulate persistence
        serialized = original_agent.model_dump_json()
        deserialized_agent = AgentBase.model_validate_json(serialized)

        # Create runtime agent with same configuration
        llm2 = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        runtime_agent = Agent(llm=llm2, tools=tools)

        # Should resolve successfully
        resolved = runtime_agent.resolve_diff_from_deserialized(deserialized_agent)
        # Test model_dump equality
        assert resolved.model_dump(mode="json") == runtime_agent.model_dump(mode="json")
        assert resolved.llm.model == runtime_agent.llm.model
        assert resolved.__class__ == runtime_agent.__class__

        # Now simulate CLI injecting dynamic litellm_extra_body metadata at load time
        injected = deserialized_agent.model_copy(
            update={
                "llm": deserialized_agent.llm.model_copy(
                    update={
                        "litellm_extra_body": {
                            "metadata": {
                                "session_id": "sess-123",
                                "tags": ["app:openhands", "model:gpt-4o-mini"],
                                "trace_version": "1.2.3",
                            }
                        }
                    }
                )
            }
        )

        # Reconcile again: differences in litellm_extra_body should be allowed and
        # the runtime value should be preferred without raising an error.
        resolved2 = runtime_agent.resolve_diff_from_deserialized(injected)
        assert resolved2.llm.litellm_extra_body == runtime_agent.llm.litellm_extra_body


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_conversation_persistence_lifecycle(mock_completion):
    """Test full conversation persistence lifecycle similar to examples/10_persistence.py."""  # noqa: E501
    from tests.conftest import create_mock_litellm_response

    # Mock the LLM completion call
    mock_response = create_mock_litellm_response(
        content="I'll help you with that task.", finish_reason="stop"
    )
    mock_completion.return_value = mock_response

    with tempfile.TemporaryDirectory() as temp_dir:
        tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=tools)

        # Create conversation and send messages
        conversation = LocalConversation(
            agent=agent, workspace=temp_dir, persistence_dir=temp_dir, visualize=False
        )

        # Send first message
        conversation.send_message(
            Message(role="user", content=[TextContent(text="First message")])
        )
        conversation.run()

        # Send second message
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Second message")])
        )
        conversation.run()

        # Store conversation ID and event count
        original_id = conversation.id
        original_event_count = len(conversation.state.events)
        original_state_dump = conversation._state.model_dump(
            mode="json", exclude={"events"}
        )

        # Delete conversation to simulate restart
        del conversation

        # Create new conversation (should load from persistence)
        new_conversation = LocalConversation(
            agent=agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            conversation_id=original_id,  # Use same ID to load existing state
            visualizer=None,
        )

        # Verify state was restored
        assert new_conversation.id == original_id
        # When loading from persistence, the state should be exactly the same
        assert len(new_conversation.state.events) == original_event_count
        # Test model_dump equality (excluding events which may have different timestamps)  # noqa: E501
        new_dump = new_conversation._state.model_dump(mode="json", exclude={"events"})
        assert new_dump == original_state_dump

        # Send another message to verify conversation continues
        new_conversation.send_message(
            Message(role="user", content=[TextContent(text="Third message")])
        )
        new_conversation.run()

        # Verify new event was added
        # We expect: original_event_count + 1 (system prompt from init) + 2
        # (user message + agent response)
        assert len(new_conversation.state.events) >= original_event_count + 2


def test_conversation_restart_with_different_agent_context():
    """
    Test conversation restart when agent_context differs.

    This simulates resuming an ACP conversation in regular CLI mode.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Simulate ACP mode: Create agent with user_provided_resources skill
        acp_skill = Skill(
            name="user_provided_resources",
            content=(
                "You may encounter sections labeled as user-provided additional "
                "context or resources."
            ),
            trigger=None,
        )
        acp_context = AgentContext(
            skills=[acp_skill],
            system_message_suffix=(
                "You current working directory is: /Users/jpshack/code/all-hands"
            ),
        )

        tools = [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        acp_agent = Agent(llm=llm, tools=tools, agent_context=acp_context)

        # Create conversation with ACP agent
        conversation = LocalConversation(
            agent=acp_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            visualizer=None,
        )

        # Send a message to create state
        conversation.send_message(
            Message(role="user", content=[TextContent(text="test message")])
        )

        conversation_id = conversation.state.id
        del conversation

        # Simulate regular CLI mode: Create agent without user_provided_resources skill
        # and different working directory
        cli_skill = Skill(
            name="project_info",
            content="Information about the current project",
            trigger=None,
        )
        cli_context = AgentContext(
            skills=[cli_skill],
            system_message_suffix="You current working directory is: /Users/jpshack",
        )

        cli_agent = Agent(llm=llm, tools=tools, agent_context=cli_context)

        # This should succeed - agent_context differences should be reconciled
        new_conversation = LocalConversation(
            agent=cli_agent,
            workspace=temp_dir,
            persistence_dir=temp_dir,
            conversation_id=conversation_id,
            visualizer=None,
        )

        # Verify state was loaded and agent_context was updated
        assert new_conversation.id == conversation_id
        assert len(new_conversation.state.events) > 0
        # The new conversation should use the CLI agent's context
        assert new_conversation.agent.agent_context is not None
        assert len(new_conversation.agent.agent_context.skills) == 1
        assert new_conversation.agent.agent_context.skills[0].name == "project_info"
        assert new_conversation.agent.agent_context.system_message_suffix is not None
        assert (
            "You current working directory is: /Users/jpshack"
            in new_conversation.agent.agent_context.system_message_suffix
        )
