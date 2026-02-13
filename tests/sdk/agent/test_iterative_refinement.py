"""Tests for iterative refinement functionality in CriticMixin."""

import json
from unittest.mock import MagicMock

import pytest

from openhands.sdk.agent.critic_mixin import (
    ITERATIVE_REFINEMENT_ITERATION_KEY,
    CriticMixin,
)
from openhands.sdk.critic.base import (
    CriticBase,
    CriticResult,
    IterativeRefinementConfig,
)
from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction


class MockCritic(CriticBase):
    """Mock critic for testing."""

    def evaluate(self, events, git_patch=None):
        return CriticResult(score=0.5, message="Mock evaluation")


class MockCriticMixin(CriticMixin):
    """Concrete implementation of CriticMixin for testing."""

    def __init__(self, critic=None):
        self.critic = critic


def create_mock_conversation(iteration: int = 0):
    """Create a mock conversation with agent_state dict."""
    mock_state = MagicMock()
    mock_state.agent_state = {}
    if iteration > 0:
        mock_state.agent_state = {ITERATIVE_REFINEMENT_ITERATION_KEY: iteration}

    mock_conversation = MagicMock()
    mock_conversation.state = mock_state
    return mock_conversation


def create_finish_action_event(critic_result: CriticResult | None = None):
    """Create a FinishAction event with optional critic result."""
    finish_action = FinishAction(message="Task completed")
    event = ActionEvent(
        thought=[TextContent(text="Finishing task")],
        action=finish_action,
        tool_name="finish",
        tool_call_id="finish_id",
        tool_call=MessageToolCall(
            id="finish_id",
            name="finish",
            arguments=json.dumps({"message": "Task completed"}),
            origin="completion",
        ),
        llm_response_id="resp_finish",
    )
    # Set critic result if provided
    if critic_result is not None:
        # Use object.__setattr__ to bypass frozen model
        object.__setattr__(event, "critic_result", critic_result)
    return event


class TestIterativeRefinementConfig:
    """Tests for IterativeRefinementConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = IterativeRefinementConfig()
        assert config.success_threshold == 0.6
        assert config.max_iterations == 3

    def test_custom_values(self):
        """Test custom configuration values."""
        config = IterativeRefinementConfig(
            success_threshold=0.8,
            max_iterations=5,
        )
        assert config.success_threshold == 0.8
        assert config.max_iterations == 5

    def test_threshold_validation_bounds(self):
        """Test that threshold must be between 0 and 1."""
        # Valid bounds
        IterativeRefinementConfig(success_threshold=0.0)
        IterativeRefinementConfig(success_threshold=1.0)

        # Invalid bounds
        with pytest.raises(Exception):  # Pydantic ValidationError
            IterativeRefinementConfig(success_threshold=-0.1)
        with pytest.raises(Exception):
            IterativeRefinementConfig(success_threshold=1.1)

    def test_max_iterations_validation(self):
        """Test that max_iterations must be at least 1."""
        IterativeRefinementConfig(max_iterations=1)

        with pytest.raises(Exception):  # Pydantic ValidationError
            IterativeRefinementConfig(max_iterations=0)


class TestCheckIterativeRefinement:
    """Tests for _check_iterative_refinement method."""

    def test_no_critic_returns_false(self):
        """Test that no critic means no refinement."""
        mixin = MockCriticMixin(critic=None)
        conversation = create_mock_conversation()
        event = create_finish_action_event()

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is False
        assert followup is None

    def test_no_iterative_config_returns_false(self):
        """Test that critic without iterative config means no refinement."""
        critic = MockCritic()
        critic.iterative_refinement = None
        mixin = MockCriticMixin(critic=critic)
        conversation = create_mock_conversation()
        event = create_finish_action_event()

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is False
        assert followup is None

    def test_max_iterations_reached(self):
        """Test that max iterations stops refinement."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig(max_iterations=3)
        mixin = MockCriticMixin(critic=critic)

        # Set iteration to max
        conversation = create_mock_conversation(iteration=3)
        event = create_finish_action_event(CriticResult(score=0.3, message="Low"))

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is False
        assert followup is None
        # Iteration should NOT have been incremented
        assert (
            conversation.state.agent_state.get(ITERATIVE_REFINEMENT_ITERATION_KEY) == 3
        )

    def test_no_critic_result_returns_false(self):
        """Test that missing critic result stops refinement."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig()
        mixin = MockCriticMixin(critic=critic)
        conversation = create_mock_conversation()
        event = create_finish_action_event(critic_result=None)

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is False
        assert followup is None

    def test_score_meets_threshold(self):
        """Test that meeting threshold stops refinement."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig(success_threshold=0.6)
        mixin = MockCriticMixin(critic=critic)
        conversation = create_mock_conversation()
        event = create_finish_action_event(CriticResult(score=0.7, message="Good"))

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is False
        assert followup is None
        # Iteration should NOT have been incremented
        assert (
            conversation.state.agent_state.get(ITERATIVE_REFINEMENT_ITERATION_KEY, 0)
            == 0
        )

    def test_score_exactly_at_threshold(self):
        """Test that score exactly at threshold is considered success."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig(success_threshold=0.6)
        mixin = MockCriticMixin(critic=critic)
        conversation = create_mock_conversation()
        event = create_finish_action_event(
            CriticResult(score=0.6, message="At threshold")
        )

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is False
        assert followup is None

    def test_score_below_threshold_continues(self):
        """Test that score below threshold triggers continuation."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig(
            success_threshold=0.6, max_iterations=3
        )
        mixin = MockCriticMixin(critic=critic)
        conversation = create_mock_conversation()
        event = create_finish_action_event(CriticResult(score=0.4, message="Low"))

        should_continue, followup = mixin._check_iterative_refinement(
            conversation, event
        )

        assert should_continue is True
        assert followup is not None
        assert "40.0%" in followup  # Score percentage in followup
        # Iteration should have been incremented
        assert (
            conversation.state.agent_state.get(ITERATIVE_REFINEMENT_ITERATION_KEY) == 1
        )

    def test_iteration_only_increments_on_continue(self):
        """Test that iteration counter only increments when continuing."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig(
            success_threshold=0.6, max_iterations=3
        )
        mixin = MockCriticMixin(critic=critic)

        # First call - score below threshold, should continue
        conversation = create_mock_conversation()
        event = create_finish_action_event(CriticResult(score=0.4, message="Low"))
        should_continue, _ = mixin._check_iterative_refinement(conversation, event)
        assert should_continue is True
        assert (
            conversation.state.agent_state.get(ITERATIVE_REFINEMENT_ITERATION_KEY) == 1
        )

        # Second call - score meets threshold, should NOT continue
        event2 = create_finish_action_event(CriticResult(score=0.7, message="Good"))
        should_continue, _ = mixin._check_iterative_refinement(conversation, event2)
        assert should_continue is False
        # Iteration should still be 1 (not incremented)
        assert (
            conversation.state.agent_state.get(ITERATIVE_REFINEMENT_ITERATION_KEY) == 1
        )

    def test_multiple_iterations(self):
        """Test multiple refinement iterations."""
        critic = MockCritic()
        critic.iterative_refinement = IterativeRefinementConfig(
            success_threshold=0.8, max_iterations=5
        )
        mixin = MockCriticMixin(critic=critic)
        conversation = create_mock_conversation()

        # Simulate multiple iterations with improving scores
        scores = [0.3, 0.5, 0.6, 0.75, 0.85]
        for i, score in enumerate(scores):
            event = create_finish_action_event(
                CriticResult(score=score, message=f"Score {score}")
            )
            should_continue, _ = mixin._check_iterative_refinement(conversation, event)

            if score < 0.8:
                assert should_continue is True
                assert (
                    conversation.state.agent_state.get(
                        ITERATIVE_REFINEMENT_ITERATION_KEY
                    )
                    == i + 1
                )
            else:
                assert should_continue is False


class TestShouldEvaluateWithCritic:
    """Tests for _should_evaluate_with_critic method."""

    def test_no_critic_returns_false(self):
        """Test that no critic means no evaluation."""
        mixin = MockCriticMixin(critic=None)
        assert mixin._should_evaluate_with_critic(None) is False
        assert mixin._should_evaluate_with_critic(FinishAction(message="done")) is False

    def test_all_actions_mode(self):
        """Test that all_actions mode evaluates everything."""
        critic = MockCritic()
        critic.mode = "all_actions"
        mixin = MockCriticMixin(critic=critic)

        assert mixin._should_evaluate_with_critic(None) is True
        assert mixin._should_evaluate_with_critic(FinishAction(message="done")) is True

    def test_finish_and_message_mode(self):
        """Test that finish_and_message mode only evaluates FinishAction."""
        critic = MockCritic()
        critic.mode = "finish_and_message"
        mixin = MockCriticMixin(critic=critic)

        assert mixin._should_evaluate_with_critic(None) is False
        assert mixin._should_evaluate_with_critic(FinishAction(message="done")) is True
