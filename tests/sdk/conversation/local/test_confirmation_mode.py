"""
Unit tests for confirmation mode functionality.

Tests the core behavior: pause action execution for user confirmation.
"""

from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import MagicMock, Mock, patch

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
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import UserRejectObservation
from openhands.sdk.llm import (
    LLM,
    ImageContent,
    Message,
    MessageToolCall,
    MetricsSnapshot,
    TextContent,
)
from openhands.sdk.llm.utils.metrics import TokenUsage
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.tool import (
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.tool.schema import Action, Observation


class MockConfirmationModeAction(Action):
    """Mock action schema for testing."""

    command: str


class MockConfirmationModeObservation(Observation):
    """Mock observation schema for testing."""

    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


class TestExecutor(
    ToolExecutor[MockConfirmationModeAction, MockConfirmationModeObservation]
):
    """Test executor for confirmation mode testing."""

    def __call__(
        self,
        action: MockConfirmationModeAction,
        conversation=None,  # noqa: ARG002
    ) -> MockConfirmationModeObservation:
        return MockConfirmationModeObservation(result=f"Executed: {action.command}")


class ConfirmationTestTool(
    ToolDefinition[MockConfirmationModeAction, MockConfirmationModeObservation]
):
    """Concrete tool for confirmation mode testing."""

    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["ConfirmationTestTool"]:
        return [
            cls(
                description="A test tool",
                action_type=MockConfirmationModeAction,
                observation_type=MockConfirmationModeObservation,
                executor=TestExecutor(),
            )
        ]


def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
    """Factory function for creating test tools."""
    return ConfirmationTestTool.create(conv_state, **params)


class TestConfirmationMode:
    """Test suite for confirmation mode functionality."""

    def setup_method(self):
        """Set up test fixtures."""

        # Create a real LLM instance for Agent validation
        self.llm: LLM = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )

        # Create a MagicMock to override the completion method
        self.mock_llm: Mock = MagicMock()

        # Create a proper MetricsSnapshot mock for the LLM
        mock_token_usage = TokenUsage(
            model="test-model",
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=4096,
            per_turn_token=150,
            response_id="test-response-id",
        )
        mock_metrics_snapshot = MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.00075,
            max_budget_per_task=None,
            accumulated_token_usage=mock_token_usage,
        )
        self.mock_llm.metrics.get_snapshot.return_value = mock_metrics_snapshot

        register_tool("test_tool", _make_tool)

        self.agent: Agent = Agent(
            llm=self.llm,
            tools=[Tool(name="test_tool")],
        )
        self.conversation: LocalConversation = Conversation(agent=self.agent)

    def _mock_message_only(self, text: str = "Hello, how can I help you?") -> MagicMock:
        """Configure LLM to return a plain assistant message (no tool calls)."""
        return MagicMock(
            return_value=ModelResponse(
                id="response_msg",
                choices=[
                    Choices(message=LiteLLMMessage(role="assistant", content=text))
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )
        )

    def _make_pending_action(self) -> None:
        """Enable confirmation mode and produce a single pending action."""
        self.conversation.set_confirmation_policy(AlwaysConfirm())
        mock_completion = self._mock_action_once()
        with patch(
            "openhands.sdk.llm.llm.litellm_completion",
            return_value=mock_completion.return_value,
        ):
            self.conversation.send_message(
                Message(role="user", content=[TextContent(text="execute a command")])
            )
            self.conversation.run()
        assert self.conversation.state.confirmation_policy == AlwaysConfirm()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        )

    def _mock_action_once(
        self, call_id: str = "call_1", command: str = "test_command"
    ) -> MagicMock:
        """Configure LLM to return one tool call (action)."""
        litellm_tool_call = ChatCompletionMessageToolCall(
            id=call_id,
            type="function",
            function=Function(
                name="test_tool",
                arguments=f'{{"command": "{command}"}}',
            ),
        )
        return MagicMock(
            return_value=ModelResponse(
                id="response_action",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content=f"I'll execute {command}",
                            tool_calls=[litellm_tool_call],
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )
        )

    def _mock_finish_action(self, message: str = "Task completed") -> MagicMock:
        """Configure LLM to return a FinishAction tool call."""
        tool_call = ChatCompletionMessageToolCall(
            id="finish_call_1",
            type="function",
            function=Function(
                name="finish",
                arguments=f'{{"message": "{message}"}}',
            ),
        )

        return MagicMock(
            return_value=ModelResponse(
                id="response_finish",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content=f"I'll finish with: {message}",
                            tool_calls=[tool_call],
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )
        )

    def _mock_think_action(self, thought: str = "Let me think about this") -> MagicMock:
        """Configure LLM to return a ThinkAction tool call."""
        tool_call = ChatCompletionMessageToolCall(
            id="think_call_1",
            type="function",
            function=Function(
                name="think",
                arguments=f'{{"thought": "{thought}"}}',
            ),
        )

        return MagicMock(
            return_value=ModelResponse(
                id="response_think",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content=f"I'll think: {thought}",
                            tool_calls=[tool_call],
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )
        )

    def _mock_multiple_actions_with_finish(self) -> MagicMock:
        """Configure LLM to return both a regular action and a FinishAction."""
        regular_tool_call = ChatCompletionMessageToolCall(
            id="call_1",
            type="function",
            function=Function(
                name="test_tool",
                arguments='{"command": "test_command"}',
            ),
        )

        finish_tool_call = ChatCompletionMessageToolCall(
            id="finish_call_1",
            type="function",
            function=Function(
                name="finish",
                arguments='{"message": "Task completed!"}',
            ),
        )

        return MagicMock(
            return_value=ModelResponse(
                id="response_multiple",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content="I'll execute the command and then finish",
                            tool_calls=[
                                regular_tool_call,
                                finish_tool_call,
                            ],
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )
        )

    def _create_test_action(self, call_id="call_1", command="test_command"):
        """Helper to create test action events."""
        action = MockConfirmationModeAction(command=command)

        litellm_tool_call = ChatCompletionMessageToolCall(
            id=call_id,
            type="function",
            function=Function(
                name="test_tool",
                arguments=f'{{"command": "{command}"}}',
            ),
        )

        # Convert to MessageToolCall for ActionEvent
        tool_call = MessageToolCall.from_chat_tool_call(litellm_tool_call)

        action_event = ActionEvent(
            source="agent",
            thought=[TextContent(text="Test thought")],
            action=action,
            tool_name="test_tool",
            tool_call_id=call_id,
            tool_call=tool_call,
            llm_response_id="response_1",
        )

        return action_event

    def test_mock_observation(self):
        # First test a round trip in the context of Observation
        obs = MockConfirmationModeObservation(result="executed")

        # Now test embeddding this into an ObservationEvent
        event = ObservationEvent(
            observation=obs,
            action_id="action_id",
            tool_name="hammer",
            tool_call_id="tool_call_id",
        )
        dumped_event = event.model_dump()
        assert dumped_event["observation"]["kind"] == "MockConfirmationModeObservation"
        assert dumped_event["observation"]["result"] == "executed"
        loaded_event = event.model_validate(dumped_event)
        loaded_obs = loaded_event.observation
        assert isinstance(loaded_obs, MockConfirmationModeObservation)
        assert loaded_obs.result == "executed"

    def test_confirmation_mode_basic_functionality(self):
        """Test basic confirmation mode operations."""
        # Test initial state
        assert self.conversation.state.confirmation_policy == NeverConfirm()
        assert (
            self.conversation.state.execution_status == ConversationExecutionStatus.IDLE
        )
        assert (
            ConversationState.get_unmatched_actions(self.conversation.state.events)
            == []
        )

        # Enable confirmation mode
        self.conversation.set_confirmation_policy(AlwaysConfirm())
        assert self.conversation.state.confirmation_policy == AlwaysConfirm()

        # Disable confirmation mode
        self.conversation.set_confirmation_policy(NeverConfirm())
        assert self.conversation.state.confirmation_policy == NeverConfirm()

        # Test rejecting when no actions exist doesn't raise error
        self.conversation.reject_pending_actions("Nothing to reject")
        rejection_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, UserRejectObservation)
        ]
        assert len(rejection_events) == 0

    def test_getting_unmatched_events(self):
        """Test getting unmatched events (actions without observations)."""
        # Create test action
        action_event = self._create_test_action()
        events: list[Event] = [action_event]

        # Test: action without observation should be pending
        unmatched = ConversationState.get_unmatched_actions(events)
        assert len(unmatched) == 1
        assert unmatched[0].id == action_event.id

        # Add observation for the action
        obs = MockConfirmationModeObservation(result="test result")

        obs_event = ObservationEvent(
            source="environment",
            observation=obs,
            action_id=action_event.id,
            tool_name="test_tool",
            tool_call_id="call_1",
        )
        events.append(obs_event)

        # Test: action with observation should not be pending
        unmatched = ConversationState.get_unmatched_actions(events)
        assert len(unmatched) == 0

        # Test rejection functionality
        action_event2 = self._create_test_action("call_2", "test_command_2")
        events.append(action_event2)

        # Add rejection for the second action
        rejection = UserRejectObservation(
            action_id=action_event2.id,
            tool_name="test_tool",
            tool_call_id="call_2",
            rejection_reason="Test rejection",
        )
        events.append(rejection)

        # Test: rejected action should not be pending
        unmatched = ConversationState.get_unmatched_actions(events)
        assert len(unmatched) == 0

        # Test UserRejectObservation functionality
        llm_message = rejection.to_llm_message()
        assert llm_message.role == "tool"
        assert llm_message.name == "test_tool"
        assert llm_message.tool_call_id == "call_2"
        assert isinstance(llm_message.content[0], TextContent)
        assert "Action rejected: Test rejection" in llm_message.content[0].text

    def test_message_only_in_confirmation_mode_does_not_wait(self):
        """Don't confirm agent messages."""
        self.conversation.set_confirmation_policy(AlwaysConfirm())
        mock_completion = self._mock_message_only("Hello, how can I help you?")
        with patch(
            "openhands.sdk.llm.llm.litellm_completion",
            return_value=mock_completion.return_value,
        ):
            self.conversation.send_message(
                Message(role="user", content=[TextContent(text="some prompt")])
            )
            self.conversation.run()

        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.FINISHED
        )

        msg_events = [
            e
            for e in self.conversation.state.events
            if isinstance(e, MessageEvent) and e.source == "agent"
        ]
        assert len(msg_events) == 1
        assert isinstance(msg_events[0].llm_message.content[0], TextContent)
        assert msg_events[0].llm_message.content[0].text == "Hello, how can I help you?"

    @pytest.mark.parametrize("should_reject", [True, False])
    def test_action_then_confirm_or_reject(self, should_reject: bool):
        """
        Start in confirmation mode, get a pending action, then:
        - if should_reject is False: confirm by calling conversation.run()
        - if should_reject is True: reject via conversation.reject_pending_action
        """
        # Create a single pending action
        self._make_pending_action()

        if not should_reject:
            # Confirm path per your instruction: call run() to execute pending action
            mock_completion = self._mock_message_only("Task completed successfully!")
            with patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=mock_completion.return_value,
            ):
                self.conversation.run()

            # Expect an observation (tool executed) and no rejection
            obs_events = [
                e
                for e in self.conversation.state.events
                if isinstance(e, ObservationEvent)
            ]
            assert len(obs_events) == 1
            assert obs_events[0].observation.result == "Executed: test_command"  # type: ignore[attr-defined]
            rejection_events = [
                e
                for e in self.conversation.state.events
                if isinstance(e, UserRejectObservation)
            ]
            assert len(rejection_events) == 0
            assert (
                self.conversation.state.execution_status
                == ConversationExecutionStatus.FINISHED
            )
        else:
            self.conversation.reject_pending_actions("Not safe to run")

            # Expect a rejection event and no observation
            rejection_events = [
                e
                for e in self.conversation.state.events
                if isinstance(e, UserRejectObservation)
            ]
            assert len(rejection_events) == 1
            obs_events = [
                e
                for e in self.conversation.state.events
                if isinstance(e, ObservationEvent)
            ]
            assert len(obs_events) == 0

    def test_single_finish_action_skips_confirmation_entirely(self):
        """Test that a single FinishAction skips confirmation entirely."""
        # Enable confirmation mode
        self.conversation.set_confirmation_policy(AlwaysConfirm())

        # Mock LLM to return a single FinishAction
        mock_completion = self._mock_finish_action("Task completed successfully!")

        # Send a message that should trigger the finish action
        with patch(
            "openhands.sdk.llm.llm.litellm_completion",
            return_value=mock_completion.return_value,
        ):
            self.conversation.send_message(
                Message(
                    role="user", content=[TextContent(text="Please finish the task")]
                )
            )

            # Run the conversation
            self.conversation.run()

        # Single FinishAction should skip confirmation entirely
        assert (
            self.conversation.state.confirmation_policy == AlwaysConfirm()
        )  # Still in confirmation mode
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.FINISHED
        )  # Agent should be finished

        # Should have no pending actions (FinishAction was executed immediately)
        pending_actions = ConversationState.get_unmatched_actions(
            self.conversation.state.events
        )
        assert len(pending_actions) == 0

        # Should have an observation event (action was executed)
        obs_events = [
            e for e in self.conversation.state.events if isinstance(e, ObservationEvent)
        ]
        assert len(obs_events) == 1
        # FinishObservation should contain the finish message in content
        assert obs_events[0].observation.text == "Task completed successfully!"

    def test_think_and_finish_action_skips_confirmation_entirely(self):
        """First step: ThinkAction (skips confirmation). Second step: FinishAction."""
        # Enable confirmation mode
        self.conversation.set_confirmation_policy(AlwaysConfirm())

        # 1st model call -> ThinkAction; 2nd model call -> FinishAction
        mock_think = self._mock_think_action("Let me analyze this problem")
        mock_finish = self._mock_finish_action("Analysis complete")

        with patch(
            "openhands.sdk.llm.llm.litellm_completion",
            side_effect=[mock_think.return_value, mock_finish.return_value],
        ):
            # Kick things off (LLM returns ThinkAction; should execute immediately)
            self.conversation.send_message(
                Message(
                    role="user", content=[TextContent(text="Please think about this")]
                )
            )
            self.conversation.run()

        # Still in confirmation mode overall, but both actions should have executed
        assert self.conversation.state.confirmation_policy == AlwaysConfirm()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.FINISHED
        )

        # No pending actions
        pending_actions = ConversationState.get_unmatched_actions(
            self.conversation.state.events
        )
        assert len(pending_actions) == 0

        # We should have two observations: one for ThinkAction, one for FinishAction
        obs_events = [
            e for e in self.conversation.state.events if isinstance(e, ObservationEvent)
        ]
        assert len(obs_events) == 2

        # 1) ThinkAction observation - should contain the standard message
        assert hasattr(obs_events[0].observation, "content")
        assert obs_events[0].observation.text == "Your thought has been logged."

        # 2) FinishAction observation - should contain the finish message
        assert hasattr(obs_events[1].observation, "content")
        assert obs_events[1].observation.text == "Analysis complete"

    def test_pause_during_confirmation_preserves_waiting_status(self):
        """Test that pausing during WAITING_FOR_CONFIRMATION preserves the status.

        This test reproduces the race condition issue where agent can be waiting
        for confirmation and the status is changed to paused instead. Waiting for
        confirmation is simply a special type of pause and should not be overridden.
        """
        # Create a pending action that puts agent in WAITING_FOR_CONFIRMATION state
        self._make_pending_action()

        # Verify we're in the expected state
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        )
        assert self.conversation.state.confirmation_policy == AlwaysConfirm()

        # Call pause() while in WAITING_FOR_CONFIRMATION state
        self.conversation.pause()

        # Status should remain WAITING_FOR_CONFIRMATION, not change to PAUSED
        # This is the key fix: waiting for confirmation is a special type of pause
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        )

        # Test that pause works correctly for other states
        # Reset to IDLE state
        with self.conversation._state:
            self.conversation._state.execution_status = ConversationExecutionStatus.IDLE

        # Pause from IDLE should change status to PAUSED
        self.conversation.pause()
        assert (
            self.conversation._state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        # Reset to RUNNING state
        with self.conversation._state:
            self.conversation._state.execution_status = (
                ConversationExecutionStatus.RUNNING
            )

        # Pause from RUNNING should change status to PAUSED
        self.conversation.pause()
        assert (
            self.conversation._state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

    def test_is_confirmation_mode_active_property(self):
        """Test the is_confirmation_mode_active property behavior."""
        # Initially, no security analyzer and NeverConfirm policy
        assert self.conversation.state.security_analyzer is None
        assert self.conversation.state.confirmation_policy == NeverConfirm()
        assert not self.conversation.confirmation_policy_active
        assert not self.conversation.is_confirmation_mode_active

        # Set confirmation policy to AlwaysConfirm, but still no security analyzer
        self.conversation.set_confirmation_policy(AlwaysConfirm())
        assert self.conversation.state.security_analyzer is None
        assert self.conversation.state.confirmation_policy == AlwaysConfirm()
        assert self.conversation.confirmation_policy_active
        # Still False because no security analyzer
        assert not self.conversation.is_confirmation_mode_active

        # Create agent and set security analyzer on conversation state
        from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer

        agent = Agent(
            llm=self.llm,
            tools=[Tool(name="test_tool")],
        )
        conversation_with_analyzer = Conversation(agent=agent)
        conversation_with_analyzer.set_security_analyzer(LLMSecurityAnalyzer())

        # Initially with security analyzer but NeverConfirm policy
        assert conversation_with_analyzer.state.security_analyzer is not None
        assert conversation_with_analyzer.state.confirmation_policy == NeverConfirm()
        assert not conversation_with_analyzer.confirmation_policy_active
        # False because policy is NeverConfirm
        assert not conversation_with_analyzer.is_confirmation_mode_active

        # Set confirmation policy to AlwaysConfirm with security analyzer
        conversation_with_analyzer.set_confirmation_policy(AlwaysConfirm())
        assert conversation_with_analyzer.state.security_analyzer is not None
        assert conversation_with_analyzer.state.confirmation_policy == AlwaysConfirm()
        assert conversation_with_analyzer.confirmation_policy_active
        # True because both conditions are met
        assert conversation_with_analyzer.is_confirmation_mode_active
