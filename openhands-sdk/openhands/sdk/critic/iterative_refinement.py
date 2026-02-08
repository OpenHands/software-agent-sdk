"""Iterative refinement utilities for critic-based agent evaluation.

This module provides utilities for running agents with iterative refinement
based on critic feedback. The main components are:

- CriticResultCollector: Collects critic results from conversation events
- IterativeRefinement: Runs an agent iteratively until success threshold is met

Example usage:
    from openhands.sdk.critic import IterativeRefinement, CriticResultCollector

    # Create the refinement runner
    refinement = IterativeRefinement(
        success_threshold=0.7,
        max_iterations=3,
        followup_prompt_fn=my_followup_prompt_generator,
    )

    # Create conversation with the collector's callback
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[refinement.collector.callback],
    )

    # Run with iterative refinement
    result = refinement.run(conversation, initial_prompt)

    # Check results
    if result.success:
        print(f"Task completed in {result.iterations} iterations")
    else:
        print(f"Task failed after {result.iterations} iterations")
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openhands.sdk.critic.result import CriticResult
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.conversation import BaseConversation
    from openhands.sdk.event import Event


logger = get_logger(__name__)


# Type alias for follow-up prompt generator function
FollowupPromptFn = Callable[[CriticResult, int], str]
"""Function that generates a follow-up prompt based on critic result and iteration."""


class CriticResultCollector:
    """Collects critic results from conversation events via callback.

    This class provides a callback that can be registered with a Conversation
    to capture critic results as they are generated during agent execution.

    Example:
        collector = CriticResultCollector()
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            callbacks=[collector.callback],
        )
        conversation.run()

        # Access collected results
        if collector.latest_result:
            print(f"Final score: {collector.latest_result.score}")
    """

    def __init__(self, verbose: bool = True) -> None:
        """Initialize the collector.

        Args:
            verbose: If True, print critic scores as they are collected.
        """
        self.results: list[CriticResult] = []
        self.latest_result: CriticResult | None = None
        self.verbose = verbose

    def callback(self, event: "Event") -> None:
        """Callback to capture critic results from events.

        This method should be passed to Conversation's callbacks parameter.
        """
        # Import at runtime to avoid circular imports
        from openhands.sdk.event import ActionEvent, MessageEvent

        if isinstance(event, (ActionEvent, MessageEvent)):
            if event.critic_result is not None:
                self.results.append(event.critic_result)
                self.latest_result = event.critic_result
                if self.verbose:
                    logger.info(f"Critic Score: {event.critic_result.score:.3f}")
                    if event.critic_result.message:
                        msg_preview = event.critic_result.message[:100]
                        logger.debug(f"Critic Details: {msg_preview}...")

    def reset(self) -> None:
        """Reset collected results for a new iteration."""
        self.results = []
        self.latest_result = None

    @property
    def best_score(self) -> float:
        """Return the best (highest) score from all collected results."""
        if not self.results:
            return 0.0
        return max(r.score for r in self.results)

    @property
    def average_score(self) -> float:
        """Return the average score from all collected results."""
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)


@dataclass
class IterativeRefinementResult:
    """Result of an iterative refinement run.

    Attributes:
        success: Whether the success threshold was met.
        iterations: Number of iterations completed.
        final_score: The final critic score (or 0.0 if no results).
        all_scores: List of scores from each iteration.
        final_critic_result: The final CriticResult object (or None).
    """

    success: bool
    iterations: int
    final_score: float
    all_scores: list[float] = field(default_factory=list)
    final_critic_result: CriticResult | None = None


def default_followup_prompt(critic_result: CriticResult, iteration: int) -> str:
    """Generate a default follow-up prompt based on critic feedback.

    This is a simple default implementation. Users should provide their own
    followup_prompt_fn for task-specific guidance.

    Args:
        critic_result: The critic result from the previous iteration.
        iteration: The current iteration number (1-indexed).

    Returns:
        A follow-up prompt string.
    """
    score_percent = critic_result.score * 100

    # Extract potential issues from critic metadata if available
    issues = []
    if critic_result.metadata and "categorized_features" in critic_result.metadata:
        categorized = critic_result.metadata["categorized_features"]
        if "agent_behavioral_issues" in categorized:
            issues = [
                f.get("display_name", f.get("name", "Unknown issue"))
                for f in categorized["agent_behavioral_issues"]
            ]

    issues_text = ""
    if issues:
        issues_text = f"\nPotential issues identified: {', '.join(issues)}"

    return (
        f"The task appears incomplete (iteration {iteration}, "
        f"success likelihood: {score_percent:.1f}%).\n"
        f"{issues_text}\n\n"
        "Please review what you've done and verify each requirement is met.\n"
        "List what's working and what needs fixing, then complete the task.\n"
    )


class IterativeRefinement:
    """Runs an agent with iterative refinement based on critic feedback.

    This class manages the iterative refinement loop:
    1. Send initial prompt and run the agent
    2. Check critic score against success threshold
    3. If below threshold, generate follow-up prompt and run again
    4. Repeat until success or max iterations reached

    Example:
        # Create the refinement runner
        refinement = IterativeRefinement(
            success_threshold=0.7,
            max_iterations=3,
        )

        # Create conversation with the collector's callback
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            callbacks=[refinement.collector.callback],
        )

        # Run with iterative refinement
        result = refinement.run(conversation, "Create a Python calculator module...")

        if result.success:
            print(f"Completed in {result.iterations} iterations")
        else:
            print(f"Failed after {result.iterations} iterations")
    """

    def __init__(
        self,
        success_threshold: float = 0.6,
        max_iterations: int = 3,
        followup_prompt_fn: FollowupPromptFn | None = None,
        verbose: bool = True,
    ) -> None:
        """Initialize the iterative refinement runner.

        Args:
            success_threshold: Score threshold (0-1) to consider task successful.
            max_iterations: Maximum number of iterations before giving up.
            followup_prompt_fn: Optional function to generate follow-up prompts.
                If not provided, uses default_followup_prompt.
            verbose: If True, print progress information.
        """
        self.success_threshold = success_threshold
        self.max_iterations = max_iterations
        self.followup_prompt_fn = followup_prompt_fn or default_followup_prompt
        self.verbose = verbose

        # Create collector - user should add collector.callback to conversation
        self._collector = CriticResultCollector(verbose=verbose)

    @property
    def collector(self) -> CriticResultCollector:
        """Access the critic result collector.

        The collector's callback should be passed to the Conversation's
        callbacks parameter to capture critic results.
        """
        return self._collector

    def run(
        self,
        conversation: "BaseConversation",
        initial_prompt: str,
    ) -> IterativeRefinementResult:
        """Run the agent with iterative refinement.

        Args:
            conversation: The conversation to run. Should have a critic configured
                on its agent, and the collector's callback registered.
            initial_prompt: The initial task prompt to send to the agent.

        Returns:
            IterativeRefinementResult with success status and metrics.
        """
        all_scores: list[float] = []

        if self.verbose:
            logger.info("=" * 70)
            logger.info("Starting Iterative Refinement")
            logger.info("=" * 70)
            logger.info(f"Success threshold: {self.success_threshold:.0%}")
            logger.info(f"Max iterations: {self.max_iterations}")

        # Initial task
        if self.verbose:
            logger.info("\n--- Iteration 1: Initial Task ---")

        conversation.send_message(initial_prompt)
        conversation.run()

        iteration = 1
        while iteration < self.max_iterations:
            # Check critic result
            if self._collector.latest_result is None:
                if self.verbose:
                    logger.warning(
                        "No critic result available, assuming task incomplete"
                    )
                score = 0.0
            else:
                score = self._collector.latest_result.score

            all_scores.append(score)

            if self.verbose:
                logger.info(f"\nIteration {iteration} final score: {score:.3f}")

            if score >= self.success_threshold:
                if self.verbose:
                    logger.info(
                        f"Success threshold ({self.success_threshold:.0%}) met!"
                    )
                break

            # Prepare for next iteration
            iteration += 1
            last_result = self._collector.latest_result or CriticResult(
                score=0.0, message=None
            )
            self._collector.reset()

            if self.verbose:
                logger.info(f"\n--- Iteration {iteration}: Follow-up Refinement ---")
                logger.info(
                    f"Score {score:.3f} < threshold {self.success_threshold:.3f}, "
                    "sending follow-up..."
                )

            followup_prompt = self.followup_prompt_fn(last_result, iteration)
            conversation.send_message(followup_prompt)
            conversation.run()

        # Capture final score if we exited the loop without breaking
        if self._collector.latest_result is not None:
            final_score = self._collector.latest_result.score
            if len(all_scores) < iteration:
                all_scores.append(final_score)
        else:
            final_score = all_scores[-1] if all_scores else 0.0

        success = final_score >= self.success_threshold

        if self.verbose:
            logger.info("\n" + "=" * 70)
            logger.info("ITERATIVE REFINEMENT COMPLETE")
            logger.info("=" * 70)
            logger.info(f"Total iterations: {iteration}")
            logger.info(f"Final critic score: {final_score:.3f}")
            logger.info(f"Success: {'YES' if success else 'NO'}")

        return IterativeRefinementResult(
            success=success,
            iterations=iteration,
            final_score=final_score,
            all_scores=all_scores,
            final_critic_result=self._collector.latest_result,
        )

    def run_with_callback(
        self,
        conversation: "BaseConversation",
        initial_prompt: str,
        on_iteration_complete: Callable[[int, float, CriticResult | None], None]
        | None = None,
    ) -> IterativeRefinementResult:
        """Run with a callback after each iteration.

        This is useful for custom progress reporting or early termination logic.

        Args:
            conversation: The conversation to run.
            initial_prompt: The initial task prompt.
            on_iteration_complete: Optional callback called after each iteration
                with (iteration_number, score, critic_result).

        Returns:
            IterativeRefinementResult with success status and metrics.
        """
        all_scores: list[float] = []

        if self.verbose:
            logger.info("=" * 70)
            logger.info("Starting Iterative Refinement")
            logger.info("=" * 70)
            logger.info(f"Success threshold: {self.success_threshold:.0%}")
            logger.info(f"Max iterations: {self.max_iterations}")

        # Initial task
        if self.verbose:
            logger.info("\n--- Iteration 1: Initial Task ---")

        conversation.send_message(initial_prompt)
        conversation.run()

        iteration = 1
        while iteration <= self.max_iterations:
            # Check critic result
            if self._collector.latest_result is None:
                if self.verbose:
                    logger.warning(
                        "No critic result available, assuming task incomplete"
                    )
                score = 0.0
            else:
                score = self._collector.latest_result.score

            all_scores.append(score)

            # Call the iteration callback
            if on_iteration_complete:
                on_iteration_complete(iteration, score, self._collector.latest_result)

            if self.verbose:
                logger.info(f"\nIteration {iteration} final score: {score:.3f}")

            if score >= self.success_threshold:
                if self.verbose:
                    logger.info(
                        f"Success threshold ({self.success_threshold:.0%}) met!"
                    )
                break

            if iteration >= self.max_iterations:
                break

            # Prepare for next iteration
            iteration += 1
            last_result = self._collector.latest_result or CriticResult(
                score=0.0, message=None
            )
            self._collector.reset()

            if self.verbose:
                logger.info(f"\n--- Iteration {iteration}: Follow-up Refinement ---")
                logger.info(
                    f"Score {score:.3f} < threshold {self.success_threshold:.3f}, "
                    "sending follow-up..."
                )

            followup_prompt = self.followup_prompt_fn(last_result, iteration)
            conversation.send_message(followup_prompt)
            conversation.run()

        # Get final score
        final_score = all_scores[-1] if all_scores else 0.0
        success = final_score >= self.success_threshold

        if self.verbose:
            logger.info("\n" + "=" * 70)
            logger.info("ITERATIVE REFINEMENT COMPLETE")
            logger.info("=" * 70)
            logger.info(f"Total iterations: {iteration}")
            logger.info(f"Final critic score: {final_score:.3f}")
            logger.info(f"Success: {'YES' if success else 'NO'}")

        return IterativeRefinementResult(
            success=success,
            iterations=iteration,
            final_score=final_score,
            all_scores=all_scores,
            final_critic_result=self._collector.latest_result,
        )
