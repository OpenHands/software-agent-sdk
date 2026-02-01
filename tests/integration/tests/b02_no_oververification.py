"""
Test that agent updates terminal truncation limit without broad or
repetitive test runs.
"""

from __future__ import annotations

from textwrap import dedent

from tests.integration.base import TestResult
from tests.integration.behavior_utils import (
    get_conversation_summary,
)
from tests.integration.utils.behavior_helpers import (
    SoftwareAgentSDKBehaviorTest,
    append_environment_tips,
)
from tests.integration.utils.llm_judge import judge_agent_behavior


INSTRUCTION_BODY = dedent(
    """
    I want to adjust the terminal tool truncation limit, i.e. reducing
    `MAX_CMD_OUTPUT_SIZE` to 20_000. Can you help with that?
    Also adjust corresponding tests to verify the change if relevant.
    """
)
INSTRUCTION = append_environment_tips(INSTRUCTION_BODY)


class NoOververificationTest(SoftwareAgentSDKBehaviorTest):
    """Ensure the agent updates truncation limit with scoped verification."""

    INSTRUCTION: str = INSTRUCTION

    def verify_result(self) -> TestResult:
        conversation_summary = get_conversation_summary(self.collected_events)
        evaluation_criteria = dedent(
            """
            Verify that the agent did not over-verify the truncation limit change
            by running test suites much broader than necessary, or repeatedly.

            The agent must:
            1. Update `MAX_CMD_OUTPUT_SIZE` to 20_000.
            2. Optionally execute only the targeted pytest command. In this case acceptable tests are ALL files under `tests/tools/terminal` (testing for the whole terminal package is not too broad here).
            3. Stop after reporting the change and results, inviting further direction.

            Did the agent follow these rules without unnecessary verification?
            """  # noqa: E501
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
                    "Agent updated truncation limit with scoped verification. "
                    f"Judge reasoning: {judgment.reasoning} "
                    f"(confidence={judgment.confidence:.2f})"
                ),
            )

        return TestResult(
            success=False,
            reason=(
                "Agent did not satisfy the truncation task criteria. "
                f"Judge reasoning: {judgment.reasoning} "
                f"(confidence={judgment.confidence:.2f})"
            ),
        )
