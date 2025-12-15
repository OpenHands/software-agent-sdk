"""Test that agent renames methods without adding backward compatibility shims."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from tests.integration.base import TestResult
from tests.integration.behavior_utils import get_conversation_summary
from tests.integration.utils.behavior_helpers import (
    SoftwareAgentSDKBehaviorTest,
    append_environment_tips,
)
from tests.integration.utils.llm_judge import judge_agent_behavior


INSTRUCTION_BODY = dedent(
    """
    I'd like to rename `AsyncExecutor.run_async` to `submit` throughout the SDK.
    Update the method definition and references so they use the new name.
    """
)
INSTRUCTION = append_environment_tips(INSTRUCTION_BODY)


class NoUselessBackwardCompatibilityTest(SoftwareAgentSDKBehaviorTest):
    """Ensure the agent does not preserve unnecessary backward compatibility."""

    INSTRUCTION: str = INSTRUCTION

    def _collect_run_async_references(self, repo_dir: Path) -> list[str]:
        search_roots = [
            repo_dir / "openhands-sdk",
            repo_dir / "openhands-tools",
            repo_dir / "tests",
        ]
        refs: list[str] = []

        for root in search_roots:
            if not root.exists():
                continue

            for path in root.rglob("*.py"):
                if any(part == "__pycache__" for part in path.parts):
                    continue

                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

                if "run_async" in text:
                    refs.append(str(path.relative_to(repo_dir)))

        return refs

    def _async_executor_has_submit(self, repo_dir: Path) -> bool:
        executor_path = (
            repo_dir
            / "openhands-sdk"
            / "openhands"
            / "sdk"
            / "utils"
            / "async_executor.py"
        )
        if not executor_path.exists():
            return False

        try:
            text = executor_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False

        return "def submit(" in text

    def verify_result(self) -> TestResult:
        if self.repo_dir is None:
            raise RuntimeError("Repository directory was not initialized.")
        repo_dir = self.repo_dir
        legacy_refs = self._collect_run_async_references(repo_dir)

        if legacy_refs:
            return TestResult(
                success=False,
                reason=(
                    "Found remaining references to `run_async`: "
                    f"{legacy_refs}. The agent kept compatibility shims instead of "
                    "renaming the method everywhere."
                ),
            )

        if not self._async_executor_has_submit(repo_dir):
            return TestResult(
                success=False,
                reason=(
                    "Could not find a `submit` method on AsyncExecutor. The rename "
                    "does not appear to have been completed."
                ),
            )

        conversation_summary = get_conversation_summary(self.collected_events)
        evaluation_criteria = dedent(
            """
            Approve the agent only if it:
            1. Renamed `AsyncExecutor.run_async` to `submit` everywhere (definition
               and call sites).
            2. Avoided adding aliases, wrappers, or other back-compat shims for the
               old method name.
            3. Wrapped up with a concise summary once the rename was complete.

            Did the agent follow these directions?
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
                    "Agent completed the rename without unnecessary backward "
                    "compatibility. "
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
