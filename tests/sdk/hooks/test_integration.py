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


class TestLocalConversationHookCallbackWiring:
    """Tests that LocalConversation wires hook callbacks to event persistence."""

    def test_modified_events_with_additional_context_persisted(self, tmp_path):
        """Test that hook-modified events (with additional_context) get persisted."""
        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.conversation import LocalConversation
        from openhands.sdk.llm import LLM

        # Create a hook that adds additional_context
        script = tmp_path / "add_context.sh"
        script.write_text(
            "#!/bin/bash\n"
            'echo \'{"additionalContext": "HOOK_INJECTED_CONTEXT"}\'\n'
            "exit 0"
        )
        script.chmod(0o755)

        hook_config = HookConfig.from_dict(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": str(script)}]}
                    ]
                }
            }
        )

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        agent = Agent(llm=llm, tools=[])

        conversation = LocalConversation(
            agent=agent,
            workspace=str(tmp_path),
            hook_config=hook_config,
            visualizer=None,
        )

        conversation.send_message("Hello")

        # Verify the MODIFIED event (with extended_content) was persisted
        events = list(conversation.state.events)
        message_events = [e for e in events if isinstance(e, MessageEvent)]

        assert len(message_events) == 1
        assert len(message_events[0].extended_content) > 0
        assert any(
            "HOOK_INJECTED_CONTEXT" in c.text
            for c in message_events[0].extended_content
        )

        conversation.close()


class TestAdditionalContextInjection:
    """Tests for additional_context injection into LLM messages."""

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

        return ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=None,
        )

    def test_additional_context_appears_in_extended_content(
        self, tmp_path, mock_conversation_state
    ):
        """Test hook additional_context is injected into extended_content."""
        # Create a hook that returns additional context
        script = tmp_path / "add_context.sh"
        script.write_text(
            "#!/bin/bash\n"
            'echo \'{"additionalContext": "Important context from hook"}\'\n'
            "exit 0"
        )
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
        processed_events = []

        def capture_callback(event):
            processed_events.append(event)

        processor = HookEventProcessor(
            hook_manager=manager, original_callback=capture_callback
        )
        processor.set_conversation_state(mock_conversation_state)

        original_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hello")],
            ),
        )

        processor.on_event(original_event)

        # Check that the callback received a modified event
        assert len(processed_events) == 1
        processed_event = processed_events[0]
        assert isinstance(processed_event, MessageEvent)

        # The extended_content should contain the hook's additional context
        assert len(processed_event.extended_content) == 1
        assert processed_event.extended_content[0].text == "Important context from hook"

    def test_additional_context_appears_in_llm_message(
        self, tmp_path, mock_conversation_state
    ):
        """Test that hook additional_context appears when converting to LLM message."""
        script = tmp_path / "add_context.sh"
        script.write_text(
            '#!/bin/bash\necho \'{"additionalContext": "Injected by hook"}\'\nexit 0'
        )
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
        processed_events = []

        def capture_callback(event):
            processed_events.append(event)

        processor = HookEventProcessor(
            hook_manager=manager, original_callback=capture_callback
        )
        processor.set_conversation_state(mock_conversation_state)

        original_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="User message")],
            ),
        )

        processor.on_event(original_event)

        # Get the LLM message from the processed event
        processed_event = processed_events[0]
        llm_message = processed_event.to_llm_message()

        # The content should include both original message and hook context
        content_texts = [
            c.text for c in llm_message.content if isinstance(c, TextContent)
        ]
        assert "User message" in content_texts
        assert "Injected by hook" in content_texts

    def test_additional_context_preserves_existing_extended_content(
        self, tmp_path, mock_conversation_state
    ):
        """Test that hook context is appended to existing extended_content."""
        script = tmp_path / "add_context.sh"
        script.write_text(
            '#!/bin/bash\necho \'{"additionalContext": "Hook context"}\'\nexit 0'
        )
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
        processed_events = []

        def capture_callback(event):
            processed_events.append(event)

        processor = HookEventProcessor(
            hook_manager=manager, original_callback=capture_callback
        )
        processor.set_conversation_state(mock_conversation_state)

        # Create event with existing extended_content
        original_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hello")],
            ),
            extended_content=[TextContent(text="Existing context")],
        )

        processor.on_event(original_event)

        processed_event = processed_events[0]

        # Both existing and hook context should be present
        assert len(processed_event.extended_content) == 2
        content_texts = [c.text for c in processed_event.extended_content]
        assert "Existing context" in content_texts
        assert "Hook context" in content_texts


class TestStopHookIntegration:
    """Tests for Stop hook integration in conversations."""

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

        return ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=workspace,
            persistence_dir=None,
        )

    def test_run_stop_with_allowing_hook(self, tmp_path, mock_conversation_state):
        """Test that run_stop returns True when hook allows stopping."""
        script = tmp_path / "allow_stop.sh"
        script.write_text('#!/bin/bash\necho \'{"decision": "allow"}\'\nexit 0')
        script.chmod(0o755)

        config = HookConfig.from_dict(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": str(script)}]}]
                }
            }
        )

        manager = HookManager(config=config, working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        should_stop, feedback = processor.run_stop(reason="finish_tool")

        assert should_stop is True
        assert feedback is None

    def test_run_stop_with_denying_hook(self, tmp_path, mock_conversation_state):
        """Test that run_stop returns False when hook denies stopping."""
        script = tmp_path / "deny_stop.sh"
        script.write_text(
            "#!/bin/bash\n"
            'echo \'{"decision": "deny", "reason": "Not done yet"}\'\n'
            "exit 2"
        )
        script.chmod(0o755)

        config = HookConfig.from_dict(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": str(script)}]}]
                }
            }
        )

        manager = HookManager(config=config, working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        should_stop, feedback = processor.run_stop(reason="finish_tool")

        assert should_stop is False
        assert feedback == "Not done yet"

    def test_run_stop_with_additional_context_as_feedback(
        self, tmp_path, mock_conversation_state
    ):
        """Test additional_context is returned as feedback when stop is denied."""
        script = tmp_path / "deny_with_feedback.sh"
        context_json = '{"decision": "deny", "additionalContext": "Please complete X"}'
        script.write_text(f"#!/bin/bash\necho '{context_json}'\nexit 2")
        script.chmod(0o755)

        config = HookConfig.from_dict(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": str(script)}]}]
                }
            }
        )

        manager = HookManager(config=config, working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        should_stop, feedback = processor.run_stop(reason="finish_tool")

        assert should_stop is False
        assert feedback == "Please complete X"

    def test_stop_hook_error_is_logged_and_allows_stop(
        self, tmp_path, mock_conversation_state
    ):
        """Test that hook errors are handled gracefully and stopping is allowed."""
        script = tmp_path / "error_hook.sh"
        script.write_text("#!/bin/bash\nexit 1")  # Non-blocking error exit
        script.chmod(0o755)

        config = HookConfig.from_dict(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": str(script)}]}]
                }
            }
        )

        manager = HookManager(config=config, working_dir=str(tmp_path))
        processor = HookEventProcessor(hook_manager=manager)
        processor.set_conversation_state(mock_conversation_state)

        should_stop, feedback = processor.run_stop(reason="finish_tool")

        # Error exit (1) doesn't block, so stopping should proceed
        assert should_stop is True
        assert feedback is None


class TestStopHookConversationIntegration:
    """Integration tests for Stop hook in LocalConversation run loop."""

    def test_stop_hook_denial_injects_feedback_and_continues(self, tmp_path):
        """Test stop hook denial injects feedback and continues loop."""
        from unittest.mock import patch

        from pydantic import SecretStr

        from openhands.sdk.agent import Agent
        from openhands.sdk.conversation import LocalConversation
        from openhands.sdk.conversation.state import ConversationExecutionStatus
        from openhands.sdk.llm import LLM

        # Create a stop hook that denies stopping the first time, then allows
        stop_count_file = tmp_path / "stop_count"
        stop_count_file.write_text("0")

        script = tmp_path / "conditional_stop.sh"
        script.write_text(f"""#!/bin/bash
count=$(cat {stop_count_file})
new_count=$((count + 1))
echo $new_count > {stop_count_file}

if [ "$count" -eq "0" ]; then
    echo '{{"decision": "deny", "additionalContext": "Complete the task first"}}'
    exit 2
else
    echo '{{"decision": "allow"}}'
    exit 0
fi
""")
        script.chmod(0o755)

        hook_config = HookConfig.from_dict(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": str(script)}]}]
                }
            }
        )

        llm = LLM(model="test-model", api_key=SecretStr("test-key"))
        agent = Agent(llm=llm, tools=[])

        # Track events
        events_captured = []

        def capture_event(event):
            events_captured.append(event)

        # Create a mock agent that sets FINISHED immediately
        step_count = 0

        def mock_step(self, conversation, on_event, on_token=None):
            nonlocal step_count
            step_count += 1
            # Always set to FINISHED - the stop hook integration should handle this
            conversation.state.execution_status = ConversationExecutionStatus.FINISHED

        with patch.object(Agent, "step", mock_step):
            conversation = LocalConversation(
                agent=agent,
                workspace=tmp_path,
                hook_config=hook_config,
                callbacks=[capture_event],
                visualizer=None,
                max_iteration_per_run=10,
            )

            # Send a message to start
            conversation.send_message("Hello")

            # Run the conversation
            conversation.run()

            # Close to trigger session end
            conversation.close()

        # The agent should have been called twice:
        # 1. First step sets FINISHED, stop hook denies, feedback injected
        # 2. Second step sets FINISHED, stop hook allows, conversation ends
        assert step_count == 2

        # Check that feedback was injected as a user message with prefix
        feedback_messages = [
            e
            for e in events_captured
            if isinstance(e, MessageEvent)
            and e.source == "user"
            and any(
                "[Stop hook feedback] Complete the task first" in c.text
                for c in e.llm_message.content
                if isinstance(c, TextContent)
            )
        ]
        assert len(feedback_messages) == 1, "Feedback message should be injected once"
