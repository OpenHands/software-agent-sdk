"""Test that the agent provides a concise explanation for each tool call."""

from __future__ import annotations

from textwrap import dedent

from tests.integration.base import TestResult
from tests.integration.behavior_utils import (
    get_conversation_summary,
    verify_all_actions_have_summary,
)
from tests.integration.utils.behavior_helpers import (
    SoftwareAgentSDKBehaviorTest,
    append_environment_tips,
)
from tests.integration.utils.llm_judge import judge_agent_behavior


INSTRUCTION_BODY = dedent(
    """
    The project is at version 1.4.1, and I'd like to bump it to 1.4.2
    throughout the SDK. Please update the version across the repo, I
    remember mostly in `pyproject.toml` and lock files.
    """
)
INSTRUCTION = append_environment_tips(INSTRUCTION_BODY)


class EachToolCallHavingExplanation(SoftwareAgentSDKBehaviorTest):
    """
    Ensure the agent provide a concise explanation for each tool
    call instead of being silent.
    """

    INSTRUCTION: str = INSTRUCTION

    def verify_result(self) -> TestResult:
        if self.repo_dir is None:
            raise RuntimeError("Repository directory was not initialized.")

        # First, verify all actions have summary fields populated
        # This is a hard requirement - the summary field should always be present
        summary_check_passed, summary_check_reason = verify_all_actions_have_summary(
            self.collected_events
        )
        if not summary_check_passed:
            return TestResult(
                success=False,
                reason=f"Summary field check failed: {summary_check_reason}",
            )

        # Then use LLM judge to evaluate the quality of explanations
        conversation_summary = get_conversation_summary(self.collected_events)
        evaluation_criteria = dedent(
            """
            Approve the agent only if it:
            1. Provides a concise explanation for each tool call. It is acceptable
            if the explanation seems vague or repetitive, we want to test for existence.
            Did the agent exhibit those behaviors?
            """
        )

        judgment = judge_agent_behavior(
            user_instruction=INSTRUCTION,
            conversation_summary=conversation_summary,
            evaluation_criteria=evaluation_criteria,
        )

        self.add_judge_usage(
            prompt_tokens=judgment.prompt_tokens,
            completion_tokens=judgment.completion_tokens,
            cost=judgment.cost,
        )

        if judgment.approved:
            return TestResult(
                success=True,
                reason=(
                    f"All actions have summaries ({summary_check_reason}). "
                    "Agent provided a concise explanation for each tool call. "
                    f"Judge reasoning: {judgment.reasoning} "
                    f"(confidence={judgment.confidence:.2f})"
                ),
            )

        return TestResult(
            success=False,
            reason=(
                "Agent behavior was not acceptable according to the LLM judge. "
                "Judge reasoning: "
                f"{judgment.reasoning} "
                f"(confidence={judgment.confidence:.2f})"
            ),
        )
