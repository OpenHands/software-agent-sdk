"""Tests for iterative refinement utilities."""

from unittest.mock import MagicMock

import pytest

from openhands.sdk.critic import (
    CriticResult,
    CriticResultCollector,
    IterativeRefinement,
    IterativeRefinementResult,
    default_followup_prompt,
)
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import MessageToolCall, TextContent


class TestCriticResultCollector:
    """Tests for CriticResultCollector."""

    def test_init_defaults(self):
        """Test default initialization."""
        collector = CriticResultCollector()
        assert collector.results == []
        assert collector.latest_result is None
        assert collector.verbose is True

    def test_init_verbose_false(self):
        """Test initialization with verbose=False."""
        collector = CriticResultCollector(verbose=False)
        assert collector.verbose is False

    def test_callback_captures_action_event_critic_result(self):
        """Test that callback captures critic results from ActionEvent."""
        collector = CriticResultCollector(verbose=False)

        critic_result = CriticResult(score=0.75, message="Good progress")
        event = ActionEvent(
            thought=[TextContent(text="thinking")],
            tool_name="test",
            tool_call_id="test_id",
            tool_call=MessageToolCall(
                id="test_id",
                name="test",
                arguments="{}",
                origin="completion",
            ),
            llm_response_id="resp_123",
            critic_result=critic_result,
        )

        collector.callback(event)

        assert len(collector.results) == 1
        assert collector.results[0] == critic_result
        assert collector.latest_result == critic_result

    def test_callback_captures_message_event_critic_result(self):
        """Test that callback captures critic results from MessageEvent."""
        from openhands.sdk.llm import Message

        collector = CriticResultCollector(verbose=False)

        critic_result = CriticResult(score=0.85, message="Almost done")
        # MessageEvent requires source and llm_message fields
        event = MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="Agent message")],
            ),
            llm_response_id="resp_456",
            critic_result=critic_result,
        )

        collector.callback(event)

        assert len(collector.results) == 1
        assert collector.results[0] == critic_result
        assert collector.latest_result == critic_result

    def test_callback_ignores_events_without_critic_result(self):
        """Test that callback ignores events without critic results."""
        collector = CriticResultCollector(verbose=False)

        event = ActionEvent(
            thought=[TextContent(text="thinking")],
            tool_name="test",
            tool_call_id="test_id",
            tool_call=MessageToolCall(
                id="test_id",
                name="test",
                arguments="{}",
                origin="completion",
            ),
            llm_response_id="resp_123",
            critic_result=None,
        )

        collector.callback(event)

        assert len(collector.results) == 0
        assert collector.latest_result is None

    def test_callback_captures_multiple_results(self):
        """Test that callback captures multiple critic results."""
        collector = CriticResultCollector(verbose=False)

        results = [
            CriticResult(score=0.5, message="First"),
            CriticResult(score=0.7, message="Second"),
            CriticResult(score=0.9, message="Third"),
        ]

        for i, result in enumerate(results):
            event = ActionEvent(
                thought=[TextContent(text=f"thinking {i}")],
                tool_name="test",
                tool_call_id=f"test_id_{i}",
                tool_call=MessageToolCall(
                    id=f"test_id_{i}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{i}",
                critic_result=result,
            )
            collector.callback(event)

        assert len(collector.results) == 3
        assert collector.latest_result == results[-1]

    def test_reset_clears_results(self):
        """Test that reset clears all collected results."""
        collector = CriticResultCollector(verbose=False)

        # Add some results
        for i in range(3):
            event = ActionEvent(
                thought=[TextContent(text=f"thinking {i}")],
                tool_name="test",
                tool_call_id=f"test_id_{i}",
                tool_call=MessageToolCall(
                    id=f"test_id_{i}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{i}",
                critic_result=CriticResult(score=0.5 + i * 0.1, message=f"Result {i}"),
            )
            collector.callback(event)

        assert len(collector.results) == 3
        assert collector.latest_result is not None

        collector.reset()

        assert len(collector.results) == 0
        assert collector.latest_result is None

    def test_best_score_returns_highest(self):
        """Test that best_score returns the highest score."""
        collector = CriticResultCollector(verbose=False)

        scores = [0.3, 0.9, 0.5, 0.7]
        for i, score in enumerate(scores):
            event = ActionEvent(
                thought=[TextContent(text=f"thinking {i}")],
                tool_name="test",
                tool_call_id=f"test_id_{i}",
                tool_call=MessageToolCall(
                    id=f"test_id_{i}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{i}",
                critic_result=CriticResult(score=score, message=f"Score {score}"),
            )
            collector.callback(event)

        assert collector.best_score == 0.9

    def test_best_score_empty_returns_zero(self):
        """Test that best_score returns 0.0 when no results."""
        collector = CriticResultCollector(verbose=False)
        assert collector.best_score == 0.0

    def test_average_score_calculates_correctly(self):
        """Test that average_score calculates correctly."""
        collector = CriticResultCollector(verbose=False)

        scores = [0.4, 0.6, 0.8]
        for i, score in enumerate(scores):
            event = ActionEvent(
                thought=[TextContent(text=f"thinking {i}")],
                tool_name="test",
                tool_call_id=f"test_id_{i}",
                tool_call=MessageToolCall(
                    id=f"test_id_{i}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{i}",
                critic_result=CriticResult(score=score, message=f"Score {score}"),
            )
            collector.callback(event)

        assert collector.average_score == pytest.approx(0.6)

    def test_average_score_empty_returns_zero(self):
        """Test that average_score returns 0.0 when no results."""
        collector = CriticResultCollector(verbose=False)
        assert collector.average_score == 0.0


class TestIterativeRefinementResult:
    """Tests for IterativeRefinementResult dataclass."""

    def test_basic_creation(self):
        """Test basic result creation."""
        result = IterativeRefinementResult(
            success=True,
            iterations=2,
            final_score=0.85,
        )
        assert result.success is True
        assert result.iterations == 2
        assert result.final_score == 0.85
        assert result.all_scores == []
        assert result.final_critic_result is None

    def test_full_creation(self):
        """Test result creation with all fields."""
        critic_result = CriticResult(score=0.85, message="Done")
        result = IterativeRefinementResult(
            success=True,
            iterations=3,
            final_score=0.85,
            all_scores=[0.5, 0.7, 0.85],
            final_critic_result=critic_result,
        )
        assert result.success is True
        assert result.iterations == 3
        assert result.final_score == 0.85
        assert result.all_scores == [0.5, 0.7, 0.85]
        assert result.final_critic_result == critic_result


class TestDefaultFollowupPrompt:
    """Tests for default_followup_prompt function."""

    def test_basic_prompt_generation(self):
        """Test basic prompt generation."""
        critic_result = CriticResult(score=0.4, message="Needs work")
        prompt = default_followup_prompt(critic_result, 2)

        assert "iteration 2" in prompt
        assert "40.0%" in prompt
        assert "review what you've done" in prompt.lower()

    def test_prompt_with_metadata_issues(self):
        """Test prompt generation with metadata containing issues."""
        critic_result = CriticResult(
            score=0.3,
            message="Issues found",
            metadata={
                "categorized_features": {
                    "agent_behavioral_issues": [
                        {"display_name": "Missing tests"},
                        {"name": "incomplete_implementation"},
                    ]
                }
            },
        )
        prompt = default_followup_prompt(critic_result, 1)

        assert "Missing tests" in prompt
        assert "incomplete_implementation" in prompt
        assert "Potential issues identified" in prompt

    def test_prompt_without_metadata(self):
        """Test prompt generation without metadata."""
        critic_result = CriticResult(score=0.5, message="Partial")
        prompt = default_followup_prompt(critic_result, 3)

        assert "iteration 3" in prompt
        assert "50.0%" in prompt
        # Should not have issues text
        assert "Potential issues identified" not in prompt


class TestIterativeRefinement:
    """Tests for IterativeRefinement class."""

    def test_init_defaults(self):
        """Test default initialization."""
        refinement = IterativeRefinement()

        assert refinement.success_threshold == 0.6
        assert refinement.max_iterations == 3
        assert refinement.verbose is True
        assert refinement.collector is not None

    def test_init_custom_values(self):
        """Test initialization with custom values."""

        def custom_fn(r: CriticResult, i: int) -> str:
            return f"Custom prompt {i}"

        refinement = IterativeRefinement(
            success_threshold=0.8,
            max_iterations=5,
            followup_prompt_fn=custom_fn,
            verbose=False,
        )

        assert refinement.success_threshold == 0.8
        assert refinement.max_iterations == 5
        assert refinement.followup_prompt_fn == custom_fn
        assert refinement.verbose is False

    def test_collector_property(self):
        """Test that collector property returns the internal collector."""
        refinement = IterativeRefinement()
        collector = refinement.collector

        assert isinstance(collector, CriticResultCollector)
        # Should be the same instance
        assert collector is refinement._collector

    def test_run_success_first_iteration(self):
        """Test run succeeds on first iteration."""
        refinement = IterativeRefinement(
            success_threshold=0.6,
            max_iterations=3,
            verbose=False,
        )

        # Mock conversation
        mock_conversation = MagicMock()

        # Simulate critic result being captured
        def simulate_run():
            # Simulate the callback being called with a high score
            event = ActionEvent(
                thought=[TextContent(text="done")],
                tool_name="finish",
                tool_call_id="finish_id",
                tool_call=MessageToolCall(
                    id="finish_id",
                    name="finish",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id="resp_finish",
                critic_result=CriticResult(score=0.8, message="Success"),
            )
            refinement.collector.callback(event)

        mock_conversation.run.side_effect = simulate_run

        result = refinement.run(mock_conversation, "Initial prompt")

        assert result.success is True
        assert result.iterations == 1
        assert result.final_score == 0.8
        mock_conversation.send_message.assert_called_once_with("Initial prompt")
        mock_conversation.run.assert_called_once()

    def test_run_success_after_retry(self):
        """Test run succeeds after retry."""
        refinement = IterativeRefinement(
            success_threshold=0.7,
            max_iterations=3,
            verbose=False,
        )

        mock_conversation = MagicMock()
        call_count = [0]

        def simulate_run():
            call_count[0] += 1
            # First call: low score, second call: high score
            score = 0.5 if call_count[0] == 1 else 0.85
            event = ActionEvent(
                thought=[TextContent(text="working")],
                tool_name="test",
                tool_call_id=f"test_id_{call_count[0]}",
                tool_call=MessageToolCall(
                    id=f"test_id_{call_count[0]}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{call_count[0]}",
                critic_result=CriticResult(score=score, message=f"Score {score}"),
            )
            refinement.collector.callback(event)

        mock_conversation.run.side_effect = simulate_run

        result = refinement.run(mock_conversation, "Initial prompt")

        assert result.success is True
        assert result.iterations == 2
        assert result.final_score == 0.85
        assert mock_conversation.send_message.call_count == 2
        assert mock_conversation.run.call_count == 2

    def test_run_failure_max_iterations(self):
        """Test run fails after max iterations."""
        refinement = IterativeRefinement(
            success_threshold=0.9,
            max_iterations=2,
            verbose=False,
        )

        mock_conversation = MagicMock()
        call_count = [0]

        def simulate_run():
            call_count[0] += 1
            # Always return low score
            event = ActionEvent(
                thought=[TextContent(text="working")],
                tool_name="test",
                tool_call_id=f"test_id_{call_count[0]}",
                tool_call=MessageToolCall(
                    id=f"test_id_{call_count[0]}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{call_count[0]}",
                critic_result=CriticResult(score=0.5, message="Low score"),
            )
            refinement.collector.callback(event)

        mock_conversation.run.side_effect = simulate_run

        result = refinement.run(mock_conversation, "Initial prompt")

        assert result.success is False
        assert result.iterations == 2
        assert result.final_score == 0.5
        assert mock_conversation.run.call_count == 2

    def test_run_no_critic_result(self):
        """Test run handles missing critic results."""
        refinement = IterativeRefinement(
            success_threshold=0.5,
            max_iterations=2,
            verbose=False,
        )

        mock_conversation = MagicMock()
        # Don't simulate any critic results

        result = refinement.run(mock_conversation, "Initial prompt")

        # Should fail with score 0.0
        assert result.success is False
        assert result.final_score == 0.0

    def test_run_with_callback(self):
        """Test run_with_callback calls the callback."""
        refinement = IterativeRefinement(
            success_threshold=0.8,
            max_iterations=3,
            verbose=False,
        )

        mock_conversation = MagicMock()
        callback_calls = []

        def on_iteration_complete(iteration, score, critic_result):
            callback_calls.append((iteration, score, critic_result))

        call_count = [0]

        def simulate_run():
            call_count[0] += 1
            score = 0.5 if call_count[0] == 1 else 0.9
            event = ActionEvent(
                thought=[TextContent(text="working")],
                tool_name="test",
                tool_call_id=f"test_id_{call_count[0]}",
                tool_call=MessageToolCall(
                    id=f"test_id_{call_count[0]}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{call_count[0]}",
                critic_result=CriticResult(score=score, message=f"Score {score}"),
            )
            refinement.collector.callback(event)

        mock_conversation.run.side_effect = simulate_run

        result = refinement.run_with_callback(
            mock_conversation,
            "Initial prompt",
            on_iteration_complete=on_iteration_complete,
        )

        assert result.success is True
        assert len(callback_calls) == 2
        assert callback_calls[0][0] == 1  # First iteration
        assert callback_calls[0][1] == 0.5  # First score
        assert callback_calls[1][0] == 2  # Second iteration
        assert callback_calls[1][1] == 0.9  # Second score

    def test_custom_followup_prompt_fn(self):
        """Test that custom followup_prompt_fn is used."""
        custom_prompts = []

        def custom_fn(critic_result, iteration):
            prompt = f"Custom: iteration {iteration}, score {critic_result.score}"
            custom_prompts.append(prompt)
            return prompt

        refinement = IterativeRefinement(
            success_threshold=0.9,
            max_iterations=2,
            followup_prompt_fn=custom_fn,
            verbose=False,
        )

        mock_conversation = MagicMock()
        call_count = [0]

        def simulate_run():
            call_count[0] += 1
            event = ActionEvent(
                thought=[TextContent(text="working")],
                tool_name="test",
                tool_call_id=f"test_id_{call_count[0]}",
                tool_call=MessageToolCall(
                    id=f"test_id_{call_count[0]}",
                    name="test",
                    arguments="{}",
                    origin="completion",
                ),
                llm_response_id=f"resp_{call_count[0]}",
                critic_result=CriticResult(score=0.5, message="Low"),
            )
            refinement.collector.callback(event)

        mock_conversation.run.side_effect = simulate_run

        refinement.run(mock_conversation, "Initial prompt")

        # Custom function should have been called for the follow-up
        assert len(custom_prompts) == 1
        assert "Custom: iteration 2" in custom_prompts[0]
        assert "score 0.5" in custom_prompts[0]
