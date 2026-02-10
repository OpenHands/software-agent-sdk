from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from openhands.sdk.critic.base import CriticBase, CriticResult
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk import Agent
    from openhands.sdk.event import LLMConvertibleEvent
    from openhands.sdk.hooks.config import HookDefinition
    from openhands.sdk.hooks.executor import HookResult
    from openhands.sdk.hooks.types import HookEvent

logger = get_logger(__name__)


# Type for agent factory function - allows users to provide custom agent creation
AgentFactory = Callable[[LLM], "Agent"]


def _default_agent_factory(llm: LLM) -> Agent:
    """Create a minimal agent for code review using only SDK components.

    This creates an agent with no tools - it relies on the LLM's ability
    to analyze the diff and provide feedback. For a more capable critic
    agent with file browsing tools, use get_critic_agent from openhands.tools.preset.
    """
    from openhands.sdk import Agent
    from openhands.sdk.context.condenser import LLMSummarizingCondenser

    condenser = LLMSummarizingCondenser(
        llm=llm.model_copy(update={"usage_id": "critic_condenser"}),
        max_size=50,
        keep_first=4,
    )

    return Agent(
        llm=llm.model_copy(update={"usage_id": "critic_agent"}),
        tools=[],  # No tools - pure LLM analysis
        condenser=condenser,
    )


class AgentReviewCritic(CriticBase):
    """Critic that spawns another OpenHands agent to perform a review.

    This critic creates a separate agent to review git diffs and provide
    feedback on code quality.

    The critic can be used in two ways:

    1. **Iterative Refinement**: Attach to an agent with IterativeRefinementConfig.
       The critic will automatically get the git diff from the workspace when
       the agent tries to finish.

    2. **Stop Hook**: Use with create_critic_stop_hook() to block the agent
       from finishing until the critic approves the diff.

    Args:
        llm: The LLM to use for the critic agent.
        agent_factory: Optional factory function to create the critic agent.
            If not provided, a minimal agent with no tools is created.
            For a more capable agent, use get_critic_agent from
            openhands.tools.preset.critic.
        review_style: Style of review ("roasted" or "standard").
        max_diff_chars: Maximum characters of diff to include.
        workspace_dir: Optional workspace directory to get git diff from.
            If not provided, the critic will use the current working directory.

    Example usage with iterative refinement:
        from openhands.sdk.critic import IterativeRefinementConfig
        from openhands.sdk.critic.impl.agent_review import AgentReviewCritic
        from openhands.tools.preset.critic import get_critic_agent

        critic = AgentReviewCritic(
            llm=llm,
            agent_factory=get_critic_agent,
            review_style="roasted",
            workspace_dir=str(workspace),
            iterative_refinement=IterativeRefinementConfig(
                success_threshold=1.0,
                max_iterations=3,
            ),
        )
        agent = Agent(llm=llm, tools=tools, critic=critic)
        conversation = Conversation(agent=agent, workspace=str(workspace))
        conversation.send_message("Make some changes...")
        conversation.run()  # Will automatically retry if critic score < threshold

    Example usage with callback hook:
        from openhands.sdk.critic.impl.agent_review import (
            AgentReviewCritic,
            create_critic_stop_hook,
        )
        from openhands.tools.preset.critic import get_critic_agent

        critic = AgentReviewCritic(
            llm=llm,
            agent_factory=get_critic_agent,
            review_style="roasted",
        )
        hook_config = HookConfig(
            stop=[
                HookMatcher(
                    hooks=[create_critic_stop_hook(critic, workspace_dir)]
                )
            ]
        )
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    llm: LLM
    # Exclude agent_factory from serialization and JSON schema since Callable types
    # can't be serialized. SkipJsonSchema prevents OpenAPI schema generation errors.
    agent_factory: SkipJsonSchema[
        Annotated[AgentFactory | None, Field(exclude=True)]
    ] = None

    review_style: str = "roasted"
    max_diff_chars: int = 100_000
    workspace_dir: str | None = None

    def _get_git_patch(self) -> str | None:
        """Get the git diff from the workspace directory."""
        cwd = self.workspace_dir or "."
        try:
            return subprocess.check_output(
                ["git", "diff"],
                cwd=cwd,
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to get git diff from {cwd}")
            return None

    def evaluate(
        self,
        events: Sequence[LLMConvertibleEvent],  # noqa: ARG002
        git_patch: str | None = None,
    ) -> CriticResult:
        from openhands.sdk import Conversation

        # If no git_patch provided, try to get it from the workspace
        if not git_patch or not git_patch.strip():
            git_patch = self._get_git_patch()

        if not git_patch or not git_patch.strip():
            # No changes to review - consider this a pass
            return CriticResult(
                score=1.0,
                message="No changes to review (empty git diff)",
            )

        factory = self.agent_factory or _default_agent_factory
        critic_agent = factory(self.llm)

        prompt = self._build_prompt(git_patch)
        conversation = Conversation(agent=critic_agent, workspace=".")
        conversation.send_message(prompt)
        conversation.run()

        final_text = self._extract_final_text(list(conversation.state.events))
        return self._parse_output(final_text)

    @staticmethod
    def _get_llm_convertible_event_type() -> type:
        """Lazy import of LLMConvertibleEvent to avoid circular imports."""
        from openhands.sdk.event import LLMConvertibleEvent

        return LLMConvertibleEvent

    def _build_prompt(self, git_patch: str) -> str:
        style = (
            "/codereview-roasted" if self.review_style == "roasted" else "/codereview"
        )
        patch = git_patch
        if len(patch) > self.max_diff_chars:
            patch = patch[: self.max_diff_chars] + (
                "\n\n... [diff truncated, "
                f"{len(git_patch):,} chars total, showing first "
                f"{self.max_diff_chars:,}] ...\n"
            )
        return (
            f"{style}\n\n"
            "You are a PR critic. Review the git diff and decide if it is ready "
            "to merge.\n"
            "Return your result as JSON in the last code block with keys: "
            "decision (pass|not_pass), summary.\n\n"
            "```diff\n"
            f"{patch}\n"
            "```\n"
        )

    def _extract_final_text(self, events: Sequence[object]) -> str:
        for event in reversed(list(events)):
            llm_msg = getattr(event, "llm_message", None)
            if llm_msg is None or not getattr(llm_msg, "content", None):
                continue
            parts: list[str] = []
            for c in llm_msg.content:
                if isinstance(c, TextContent):
                    parts.append(c.text)
            if parts:
                return "".join(parts)
        return ""

    def _parse_output(self, text: str) -> CriticResult:
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        candidate = blocks[-1] if blocks else None
        if candidate is None:
            m = re.search(
                r"(\{\s*\"decision\"\s*:\s*\"(pass|not_pass)\".*\})",
                text,
                re.DOTALL,
            )
            candidate = m.group(1) if m else None

        if not candidate:
            logger.warning("Critic output missing JSON block")
            return CriticResult(score=0.0, message="Critic output missing JSON")

        try:
            data = json.loads(candidate)
        except Exception as e:
            logger.warning(f"Failed to parse critic JSON: {e}")
            return CriticResult(score=0.0, message="Critic JSON parse error")

        decision = str(data.get("decision", "not_pass")).strip()
        score = 1.0 if decision == "pass" else 0.0
        summary = str(data.get("summary", "") or "").strip() or decision
        return CriticResult(score=score, message=summary)

    def get_followup_prompt(self, critic_result: CriticResult, iteration: int) -> str:
        """Generate a code-review-specific follow-up prompt for iterative refinement.

        This override provides feedback based on the code review critic's evaluation,
        including the specific issues identified in the review.

        Args:
            critic_result: The critic result from the previous iteration.
            iteration: The current iteration number (1-indexed).

        Returns:
            A follow-up prompt string with code review feedback.
        """
        lines = [
            f"## Code Review Feedback (Iteration {iteration})",
            "",
            "The code review critic has evaluated your changes and found issues:",
            "",
            f"**Review Summary:** {critic_result.message}",
            "",
            "Please address the issues identified in the review:",
            "1. Review the feedback above carefully",
            "2. Make the necessary changes to fix the identified issues",
            "3. Verify your changes address all the concerns",
            "4. Try to finish again once the issues are resolved",
            "",
        ]
        return "\n".join(lines)


def create_critic_stop_hook(
    critic: AgentReviewCritic,
    workspace_dir: str,
) -> HookDefinition:
    """Create a callback-based stop hook that runs the critic agent.

    This function creates a HookDefinition with a Python callback that:
    1. Gets the current git diff from the workspace
    2. Runs the critic agent to review the diff
    3. Returns allow/deny based on the critic's decision

    Args:
        critic: The AgentReviewCritic instance to use for review
        workspace_dir: The workspace directory to get git diff from

    Returns:
        A HookDefinition configured as a callback hook

    Example:
        critic = AgentReviewCritic(llm=llm, review_style="roasted")
        hook_config = HookConfig(
            stop=[
                HookMatcher(
                    hooks=[create_critic_stop_hook(critic, str(workspace))]
                )
            ]
        )
    """
    from openhands.sdk.hooks.config import HookDefinition, HookType
    from openhands.sdk.hooks.executor import HookResult
    from openhands.sdk.hooks.types import HookDecision

    def critic_callback(_event: HookEvent) -> HookResult:
        """Callback that runs the critic agent on the current git diff."""
        try:
            # Get the git diff from the workspace
            patch = subprocess.check_output(
                ["git", "diff"],
                cwd=workspace_dir,
                text=True,
                stderr=subprocess.DEVNULL,
            )

            # If no changes, allow stopping
            if not patch.strip():
                return HookResult(
                    success=True,
                    decision=HookDecision.ALLOW,
                )

            # Run the critic
            result = critic.evaluate(events=[], git_patch=patch)

            if result.success:
                return HookResult(
                    success=True,
                    decision=HookDecision.ALLOW,
                    additional_context=f"Critic approved: {result.message}",
                )
            else:
                return HookResult(
                    success=True,
                    blocked=True,
                    decision=HookDecision.DENY,
                    reason=f"Critic rejected: {result.message}",
                    additional_context=f"Critic feedback: {result.message}",
                )

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get git diff: {e}")
            return HookResult(
                success=True,
                decision=HookDecision.ALLOW,
                additional_context="Could not get git diff, allowing stop",
            )
        except Exception as e:
            logger.error(f"Critic callback failed: {e}")
            return HookResult(
                success=False,
                error=f"Critic callback failed: {e}",
            )

    return HookDefinition(
        type=HookType.CALLBACK,
        callback=critic_callback,
        timeout=300,  # 5 minutes for critic agent to run
    )
