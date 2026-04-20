import abc
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from openhands.sdk.critic.result import CriticResult
from openhands.sdk.utils.models import DiscriminatedUnionMixin


if TYPE_CHECKING:
    from openhands.sdk.event.base import LLMConvertibleEvent


# Type alias for follow-up prompt generator function
FollowupPromptFn = Callable[[CriticResult, int], str]
"""Function that generates a follow-up prompt based on critic result and iteration."""


class IterativeRefinementConfig(BaseModel):
    """Configuration for iterative refinement based on critic feedback.

    When attached to a CriticBase, the Conversation.run() method will
    automatically retry the task if the critic score is below the threshold.

    Example:
        critic = APIBasedCritic(
            server_url="...",
            api_key="...",
            model_name="critic",
            iterative_refinement=IterativeRefinementConfig(
                success_threshold=0.7,
                max_iterations=3,
            ),
        )
        agent = Agent(llm=llm, tools=tools, critic=critic)
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message("Create a calculator module...")
        conversation.run()  # Will automatically retry if critic score < 0.7
    """

    success_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Score threshold (0-1) to consider task successful.",
    )
    max_iterations: int = Field(
        default=3,
        ge=1,
        description="Maximum number of iterations before giving up.",
    )
    # Note: followup_prompt_fn is not serializable, so we use a default
    # Users can override by subclassing or using the IterativeRefinement class directly


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

        Args:
            critic_result: The critic result from the previous iteration.
            iteration: The current iteration number (1-indexed).

        Returns:
            A follow-up prompt string to send to the agent.
        """
        score_percent = critic_result.score * 100

        return (
            f"The task appears incomplete (iteration {iteration}, "
            f"predicted success likelihood: {score_percent:.1f}%).\n\n"
            "Please review what you've done and verify each requirement is met.\n"
            "List what's working and what needs fixing, then complete the task.\n"
        )
