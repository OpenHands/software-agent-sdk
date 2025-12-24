"""Integration tests for hooks blocking in Agent and Conversation."""

import pytest

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.hooks.conversation_hooks import (
    HookEventProcessor,
    create_hook_callback,
)
from openhands.sdk.hooks.manager import HookManager
from openhands.sdk.llm import Message, TextContent


class TestBlockedActionsState:
    """Tests for blocked_actions field on ConversationState."""

    def test_blocked_actions_field_exists(self):
        """Test that ConversationState has blocked_actions field."""
        # blocked_actions should be in the model fields
        assert "blocked_actions" in ConversationState.model_fields

    def test_blocked_actions_default_empty(self):
        """Test that blocked_actions defaults to empty dict."""
        # Create a minimal state dict for validation
        import tempfile
        import uuid

        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.llm import LLM
        from openhands.sdk.workspace import LocalWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            llm = LLM(model="test-model", api_key=SecretStr("test-key"))
            agent = Agent(llm=llm, tools=[])
            workspace = LocalWorkspace(working_dir=tmpdir)

            state = ConversationState(
                id=uuid.uuid4(),
                agent=agent,
                workspace=workspace,
                persistence_dir=None,
            )

            assert state.blocked_actions == {}


class TestBlockedStatePersistence:
    """Tests for blocked state persistence across resume."""

    def _create_persistent_state(self, tmp_path, conversation_id):
        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.llm import LLM
        from openhands.sdk.workspace import LocalWorkspace

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        agent = Agent(llm=llm, tools=[])
        workspace = LocalWorkspace(working_dir=str(tmp_path))
        persistence_dir = tmp_path / "conversations"
        return ConversationState.create(
            id=conversation_id,
            agent=agent,
            workspace=workspace,
            persistence_dir=str(persistence_dir),
        )

    def test_blocked_entries_persist_across_resume(self, tmp_path):
        import uuid

        conversation_id = uuid.uuid4()
        state = self._create_persistent_state(tmp_path, conversation_id)
        state.block_action("action-1", "Blocked")
        state.block_message("message-1", "Nope")

        resumed = self._create_persistent_state(tmp_path, conversation_id)

        assert resumed.blocked_actions["action-1"] == "Blocked"
        assert resumed.blocked_messages["message-1"] == "Nope"

    def test_blocked_entries_removal_persists(self, tmp_path):
        import uuid

        conversation_id = uuid.uuid4()
        state = self._create_persistent_state(tmp_path, conversation_id)
        state.block_action("action-1", "Blocked")
        state.block_message("message-1", "Nope")

        assert state.pop_blocked_action("action-1") == "Blocked"
        assert state.pop_blocked_message("message-1") == "Nope"

        resumed = self._create_persistent_state(tmp_path, conversation_id)

        assert "action-1" not in resumed.blocked_actions
        assert "message-1" not in resumed.blocked_messages


class TestUserPromptSubmitBlocking:
    """Tests for UserPromptSubmit hook blocking."""

    @pytest.fixture
    def mock_conversation_state(self, tmp_path):
        """Create a mock conversation state."""
        import uuid

        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.llm import LLM
        from openhands.sdk.workspace import LocalWorkspace

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        agent = Agent(llm=llm, tools=[])
        workspace = LocalWorkspace(working_dir=str(tmp_path))

        return ConversationState(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=None,
        )

    def test_is_message_blocked_without_state(self, tmp_path):
        """Test that is_message_blocked returns False without state set."""
        manager = HookManager(config=HookConfig(), working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        # No state set
        assert not processor.is_message_blocked("any-message-id")

    def test_blocking_user_prompt_hook_adds_to_state(
        self, tmp_path, mock_conversation_state
    ):
        """Test blocking UserPromptSubmit hooks add message ID to blocked_messages."""
        # Create a blocking hook script
        script = tmp_path / "block_prompt.sh"
        script.write_text('#!/bin/bash\necho "Blocked by policy" >&2\nexit 2')
        script.chmod(0o755)

        config = HookConfig.from_dict(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": str(script)}]}
                    ]
                }
            }
        )

        manager = HookManager(config=config, working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        message_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hello, this should be blocked")],
            ),
        )

        processor.on_event(message_event)

        assert processor.is_message_blocked(message_event.id)
        assert (
            "Blocked by policy"
            in mock_conversation_state.blocked_messages[message_event.id]
        )

    def test_non_blocking_user_prompt_hook_does_not_block(
        self, tmp_path, mock_conversation_state
    ):
        """Test that non-blocking hooks don't add to blocked_messages."""
        script = tmp_path / "allow_prompt.sh"
        script.write_text("#!/bin/bash\nexit 0")
        script.chmod(0o755)

        config = HookConfig.from_dict(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": str(script)}]}
                    ]
                }
            }
        )

        manager = HookManager(config=config, working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        message_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hello, this should pass")],
            ),
        )

        processor.on_event(message_event)

        assert not processor.is_message_blocked(message_event.id)


class TestHookEventProcessorBlocking:
    """Tests for HookEventProcessor blocking integration."""

    @pytest.fixture
    def blocking_config(self, tmp_path):
        """Create a config with a blocking hook."""
        script = tmp_path / "block.sh"
        script.write_text(
            '#!/bin/bash\necho \'{"decision": "deny", "reason": "Test block"}\'\nexit 2'
        )
        script.chmod(0o755)

        return HookConfig.from_dict(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": str(script)}],
                        }
                    ]
                }
            }
        )

    @pytest.fixture
    def mock_conversation_state(self, tmp_path):
        """Create a mock conversation state with blocked_actions."""
        import uuid

        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.llm import LLM
        from openhands.sdk.workspace import LocalWorkspace

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        agent = Agent(llm=llm, tools=[])
        workspace = LocalWorkspace(working_dir=str(tmp_path))

        return ConversationState(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=None,
        )

    def test_set_conversation_state(self, tmp_path, mock_conversation_state):
        """Test that set_conversation_state stores the state reference."""
        manager = HookManager(
            config=HookConfig(),
            working_dir=str(tmp_path),
        )
        processor = HookEventProcessor(hook_manager=manager)

        assert processor._conversation_state is None
        processor.set_conversation_state(mock_conversation_state)
        assert processor._conversation_state is mock_conversation_state

    def test_blocking_hook_adds_to_state(
        self, tmp_path, blocking_config, mock_conversation_state
    ):
        """Test that blocking hooks add action ID to state.blocked_actions."""
        manager = HookManager(
            config=blocking_config,
            working_dir=str(tmp_path),
        )
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        # Create a mock action event with required fields
        from openhands.sdk.llm import MessageToolCall
        from openhands.sdk.tool.builtins import ThinkAction

        action_event = ActionEvent(
            source="agent",
            tool_name="terminal",
            tool_call_id="test-call-id",
            tool_call=MessageToolCall(
                id="test-call-id", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="test-response-id",
            action=ThinkAction(thought="test"),
            thought=[],
        )

        # Process the event (this should trigger the blocking hook)
        processor.on_event(action_event)

        # Check that the action was marked as blocked
        assert action_event.id in mock_conversation_state.blocked_actions
        assert "Test block" in mock_conversation_state.blocked_actions[action_event.id]

    def test_is_action_blocked_uses_state(
        self, tmp_path, blocking_config, mock_conversation_state
    ):
        """Test that is_action_blocked checks the state."""
        manager = HookManager(
            config=blocking_config,
            working_dir=str(tmp_path),
        )
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        # Manually add a blocked action
        mock_conversation_state.blocked_actions["test-action-id"] = "Blocked"

        assert processor.is_action_blocked("test-action-id")
        assert not processor.is_action_blocked("other-action-id")

    def test_is_action_blocked_without_state(self, tmp_path):
        """Test that is_action_blocked returns False without state."""
        manager = HookManager(
            config=HookConfig(),
            working_dir=str(tmp_path),
        )
        processor = HookEventProcessor(hook_manager=manager)

        # No state set
        assert not processor.is_action_blocked("any-action-id")


class TestPostToolUseActionLookup:
    """Tests for PostToolUse looking up actions from conversation state events."""

    @pytest.fixture
    def logging_config(self, tmp_path):
        """Create a config with a PostToolUse hook that logs tool_input."""
        log_file = tmp_path / "hook_output.log"
        script = tmp_path / "log_input.sh"
        script.write_text(f"#!/bin/bash\ncat > {log_file}\nexit 0")
        script.chmod(0o755)

        return HookConfig.from_dict(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": str(script)}],
                        }
                    ]
                }
            }
        ), log_file

    @pytest.fixture
    def mock_conversation_state(self, tmp_path):
        """Create a mock conversation state using the factory method."""
        import uuid

        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.llm import LLM
        from openhands.sdk.workspace import LocalWorkspace

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        agent = Agent(llm=llm, tools=[])
        workspace = LocalWorkspace(working_dir=str(tmp_path))

        # Use create() factory to properly initialize _events
        return ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=None,
        )

    def test_post_tool_use_finds_action_from_events(
        self, tmp_path, logging_config, mock_conversation_state
    ):
        """Test that PostToolUse hooks find action from conversation.state.events."""
        import json

        from openhands.sdk.event import ObservationEvent
        from openhands.sdk.llm import MessageToolCall
        from openhands.sdk.tool.builtins import ThinkAction, ThinkObservation

        config, log_file = logging_config
        manager = HookManager(
            config=config,
            working_dir=str(tmp_path),
        )
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        # Create an action event
        action_event = ActionEvent(
            source="agent",
            tool_name="Think",
            tool_call_id="test-call-id",
            tool_call=MessageToolCall(
                id="test-call-id", name="Think", arguments="{}", origin="completion"
            ),
            llm_response_id="test-response-id",
            action=ThinkAction(thought="test thought"),
            thought=[],
        )

        # Add action to state events (simulating what Conversation does)
        mock_conversation_state.events.append(action_event)

        # Create a corresponding observation event
        observation_event = ObservationEvent(
            source="agent",
            action_id=action_event.id,  # Links to the action
            tool_name="Think",
            tool_call_id="test-call-id",
            observation=ThinkObservation(),
        )

        # Process the observation (this should trigger PostToolUse and find the action)
        processor.on_event(observation_event)

        # Verify the hook received the action's tool_input and tool_response
        assert log_file.exists(), "Hook should have been called and written to log file"
        hook_input = json.loads(log_file.read_text())
        assert hook_input["tool_name"] == "Think"
        assert "tool_input" in hook_input
        # The tool_input should contain the action's model_dump
        assert "thought" in hook_input["tool_input"]
        # The tool_response should contain the observation's model_dump
        assert "tool_response" in hook_input
        assert isinstance(hook_input["tool_response"], dict)
        assert "content" in hook_input["tool_response"]  # From Observation base class

    def test_post_tool_use_without_state_does_not_crash(self, tmp_path, logging_config):
        """Test that PostToolUse gracefully handles missing conversation state."""
        from openhands.sdk.event import ObservationEvent
        from openhands.sdk.tool.builtins import ThinkObservation

        config, log_file = logging_config
        manager = HookManager(
            config=config,
            working_dir=str(tmp_path),
        )
        processor = HookEventProcessor(hook_manager=manager)
        # Note: NOT calling set_conversation_state

        observation_event = ObservationEvent(
            source="agent",
            action_id="nonexistent-action",
            tool_name="Think",
            tool_call_id="test-call-id",
            observation=ThinkObservation(),
        )

        # Should not crash, just return early
        processor.on_event(observation_event)

        # Hook should NOT have been called (action not found)
        assert not log_file.exists()


class TestCreateHookCallback:
    """Tests for create_hook_callback function."""

    def test_create_hook_callback_returns_processor_and_callback(self, tmp_path):
        """Test that create_hook_callback returns processor and callback."""
        config = HookConfig.from_dict({"hooks": {}})

        processor, callback = create_hook_callback(
            hook_config=config,
            working_dir=str(tmp_path),
            session_id="test-session",
        )

        assert isinstance(processor, HookEventProcessor)
        assert callable(callback)
        assert callback == processor.on_event
