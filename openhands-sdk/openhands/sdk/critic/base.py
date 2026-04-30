import abc
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from openhands.sdk.critic.refinement import (
    DEFAULT_ISSUE_THRESHOLD,
    IterativeRefinementDecision,
    build_refinement_message,
    evaluate_iterative_refinement,
)
from openhands.sdk.critic.result import CriticResult
from openhands.sdk.utils.models import DiscriminatedUnionMixin


if TYPE_CHECKING:
    from openhands.sdk.event.base import LLMConvertibleEvent


# Type alias for follow-up prompt generator function
FollowupPromptFn = Callable[[CriticResult, int], str]
"""Function that generates a follow-up prompt based on critic result and iteration."""


class IterativeRefinementConfig(BaseModel):
    """Configuration for generalized critic-driven iterative refinement.

    This policy evaluates critic results, decides whether refinement should
    continue, and can build the follow-up prompt sent back to the agent.

    Example:
        critic = APIBasedCritic(
            server_url="...",
            api_key="...",
            model_name="critic",
            iterative_refinement=IterativeRefinementConfig(
                success_threshold=0.7,
                issue_threshold=0.75,
                max_iterations=3,
            ),
        )
        agent = Agent(llm=llm, tools=tools, critic=critic)
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message("Create a calculator module...")
        conversation.run()
    """

    success_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Score threshold (0-1) to consider task successful.",
    )
    issue_threshold: float = Field(
        default=DEFAULT_ISSUE_THRESHOLD,
        ge=0.0,
        le=1.0,
        description=(
            "Probability threshold for critic-detected agent issues that should "
            "trigger refinement even when the overall score is acceptable."
        ),
    )
    max_iterations: int = Field(
        default=3,
        ge=1,
        description="Maximum number of iterations before giving up.",
    )

    def evaluate(
        self, critic_result: CriticResult | None
    ) -> IterativeRefinementDecision:
        """Evaluate whether a critic result should trigger another iteration."""
        return evaluate_iterative_refinement(
            critic_result,
            success_threshold=self.success_threshold,
            issue_threshold=self.issue_threshold,
        )

    def build_followup_prompt(
        self,
        critic_result: CriticResult,
        iteration: int,
        *,
        decision: IterativeRefinementDecision | None = None,
    ) -> str:
        """Build the follow-up prompt for the next refinement iteration."""
        return build_refinement_message(
            critic_result,
            iteration,
            max_iterations=self.max_iterations,
            issue_threshold=self.issue_threshold,
            triggered_issues=(
                decision.triggered_issues if decision is not None else None
            ),
        )


class CriticBase(DiscriminatedUnionMixin, abc.ABC):
    """A critic is a function that takes in a list of events,
    optional git patch, and returns a score about the quality of agent's action.
    """

    mode: Literal["finish_and_message", "all_actions"] = Field(
        default="finish_and_message",
        description=(
            "When to run critic evaluation:\n"
            "- 'finish_and_message': Evaluate on FinishAction and agent"
            " MessageEvent (default, minimal performance impact)\n"
            "- 'all_actions': Evaluate after every agent action (WARNING: "
            "significantly slower due to API calls on each action)"
        ),
    )

    iterative_refinement: IterativeRefinementConfig | None = Field(
        default=None,
        description=(
            "Optional configuration for iterative refinement. When set, "
            "Conversation.run() will automatically retry the task if the "
            "critic score is below the success_threshold, up to max_iterations."
        ),
    )

    @abc.abstractmethod
    def evaluate(
        self, events: Sequence["LLMConvertibleEvent"], git_patch: str | None = None
    ) -> CriticResult:
        pass

    def get_followup_prompt(self, critic_result: CriticResult, iteration: int) -> str:
        """Generate a follow-up prompt for iterative refinement.

        Subclasses can override this method to provide custom follow-up prompts.
        When iterative refinement configuration is present, the default prompt
        uses that policy's shared follow-up message builder so all SDK consumers
        can reuse the same refinement architecture.
        """
        if self.iterative_refinement is not None:
            return self.iterative_refinement.build_followup_prompt(
                critic_result,
                iteration,
            )

        return build_refinement_message(critic_result, iteration)
