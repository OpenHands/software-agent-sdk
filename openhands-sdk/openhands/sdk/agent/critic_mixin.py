"""Mixin class for critic-related functionality in agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openhands.sdk.critic.base import CriticResult
from openhands.sdk.event import ActionEvent, LLMConvertibleEvent, MessageEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Action
from openhands.sdk.tool.builtins import FinishAction


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.critic.base import CriticBase


logger = get_logger(__name__)

# Key for storing iterative refinement iteration count in agent_state
ITERATIVE_REFINEMENT_ITERATION_KEY = "iterative_refinement_iteration"


class CriticMixin:
    """Mixin providing critic evaluation and iterative refinement functionality.

    This mixin is designed to be used with Agent classes that have a `critic`
    attribute of type CriticBase | None.
    """

    critic: CriticBase | None

    def _should_evaluate_with_critic(self, action: Action | None) -> bool:
        """Determine if critic should evaluate based on action type and mode."""
        if self.critic is None:
            return False

        if self.critic.mode == "all_actions":
            return True

        # For "finish_and_message" mode, only evaluate FinishAction
        # (MessageEvent will be handled separately in step())
        if isinstance(action, FinishAction):
            return True

        return False

    def _evaluate_with_critic(
        self, conversation: LocalConversation, event: ActionEvent | MessageEvent
    ) -> CriticResult | None:
        """Run critic evaluation on the current event and history."""
        if self.critic is None:
            return None

        try:
            # Build event history including the current event
            events = list(conversation.state.events) + [event]
            llm_convertible_events = [
                e for e in events if isinstance(e, LLMConvertibleEvent)
            ]

            # Evaluate without git_patch for now
            critic_result = self.critic.evaluate(
                events=llm_convertible_events, git_patch=None
            )
            logger.info(
                f"✓ Critic evaluation: score={critic_result.score:.3f}, "
                f"success={critic_result.success}"
            )
            return critic_result
        except Exception as e:
            logger.error(f"✗ Critic evaluation failed: {e}", exc_info=True)
            return None

    def _check_iterative_refinement(
        self, conversation: LocalConversation, action_event: ActionEvent
    ) -> tuple[bool, str | None]:
        """Check if iterative refinement should continue after a FinishAction.

        This method checks the critic result and determines whether to continue
        with another iteration. State mutation (incrementing the iteration counter)
        only occurs when refinement will actually continue.

        Returns:
            A tuple of (should_continue, followup_message).
            If should_continue is True, the agent should continue with the
            followup_message instead of finishing.
        """
        # Check if critic has iterative refinement config
        if self.critic is None or self.critic.iterative_refinement is None:
            return False, None

        config = self.critic.iterative_refinement
        state = conversation.state

        # Get current iteration count (0-indexed)
        iteration = state.agent_state.get(ITERATIVE_REFINEMENT_ITERATION_KEY, 0)

        # Check if we've exceeded max iterations BEFORE incrementing
        if iteration >= config.max_iterations:
            logger.info(
                f"Iterative refinement: max iterations "
                f"({config.max_iterations}) reached"
            )
            return False, None

        # Get the critic result from the action event
        critic_result = action_event.critic_result
        if critic_result is None:
            logger.warning("Iterative refinement: no critic result on FinishAction")
            return False, None

        # Check if score meets threshold
        if critic_result.score >= config.success_threshold:
            logger.info(
                f"Iterative refinement: success threshold "
                f"({config.success_threshold:.0%}) met with score "
                f"{critic_result.score:.3f}"
            )
            return False, None

        # Score below threshold AND we haven't hit max iterations
        # NOW we increment the counter since we're actually continuing
        # Use reassignment pattern to trigger autosave
        new_iteration = iteration + 1
        state.agent_state = {
            **state.agent_state,
            ITERATIVE_REFINEMENT_ITERATION_KEY: new_iteration,
        }

        logger.info(
            f"Iterative refinement: score {critic_result.score:.3f} < "
            f"threshold {config.success_threshold:.3f}, "
            f"iteration {new_iteration}/{config.max_iterations}"
        )
        followup = self.critic.get_followup_prompt(critic_result, new_iteration)
        return True, followup
