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
from tests.integration.early_stopper import EarlyStopperBase, TestExecutionPruner
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

    def get_early_stopper(self) -> EarlyStopperBase:
        """Stop early if agent runs overly broad tests.

        Detects patterns like 'pytest tests/' or 'pytest .' which indicate
        running tests much broader than the targeted terminal tests.

        Note: We allow tests targeting tests/tools/terminal/ since the
        evaluation criteria explicitly permits this scope.
        """

        # Use a custom check that allows terminal-targeted tests
        class TerminalTestAwarePruner(TestExecutionPruner):
            def check(self, events):
                from openhands.sdk.event.llm_convertible.action import ActionEvent
                from openhands.tools.terminal.definition import (
                    TerminalAction,
                    TerminalTool,
                )

                test_commands = 0
                for event in events:
                    if (
                        isinstance(event, ActionEvent)
                        and event.tool_name == TerminalTool.name
                    ):
                        if event.action is not None and isinstance(
                            event.action, TerminalAction
                        ):
                            command = event.action.command
                            if "pytest" in command or "python -m unittest" in command:
                                test_commands += 1

                                # Allow targeted terminal tests
                                if "tests/tools/terminal" in command:
                                    continue

                                # Check for overly broad patterns
                                for pattern in self.broad_test_patterns:
                                    if pattern in command:
                                        from tests.integration.early_stopper import (
                                            EarlyStopResult,
                                        )

                                        return EarlyStopResult(
                                            should_stop=True,
                                            reason=(
                                                f"Detected overly broad test command: "
                                                f"{command[:100]}"
                                            ),
                                        )

                if test_commands > self.max_test_commands:
                    from tests.integration.early_stopper import EarlyStopResult

                    return EarlyStopResult(
                        should_stop=True,
                        reason=(
                            f"Executed {test_commands} test commands, "
                            f"exceeding limit of {self.max_test_commands}"
                        ),
                    )

                from tests.integration.early_stopper import EarlyStopResult

                return EarlyStopResult(should_stop=False)

        return TerminalTestAwarePruner(
            max_test_commands=5,  # More targeted test runs allowed
            broad_test_patterns=[
                "pytest tests/",  # Running entire tests dir
                "pytest .",  # Running all tests
                "python -m pytest .",  # Running all tests
                "pytest -x tests/",  # Broad with stop-on-first
            ],
        )

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
