"""Test that agent renames methods without adding backward compatibility shims."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult
from tests.integration.behavior_utils import get_conversation_summary
from tests.integration.utils.llm_judge import judge_agent_behavior


INSTRUCTION = dedent(
    """
    I'd like to rename `AsyncExecutor.run_async` to `submit` throughout the SDK.
    Update the method definition and references so they use the new name.
    """
)

logger = get_logger(__name__)


class NoUselessBackwardCompatibilityTest(BaseIntegrationTest):
    """Ensure the agent does not preserve unnecessary backward compatibility."""

    INSTRUCTION: str = INSTRUCTION

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]

    def setup(self) -> None:  # noqa: D401
        """Set up a realistic codebase by cloning the software-agent-sdk repo."""
        try:
            repo_dir = os.path.join(self.workspace, "software-agent-sdk")
            target_commit = "693c32618dca43e6506a785da4e37575e387a638"

            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "https://github.com/OpenHands/software-agent-sdk.git",
                    repo_dir,
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )

            subprocess.run(
                [
                    "git",
                    "fetch",
                    "origin",
                    target_commit,
                    "--depth",
                    "1",
                ],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                timeout=60,
            )

            subprocess.run(
                ["git", "checkout", target_commit],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                timeout=30,
            )

            readme_path = os.path.join(self.workspace, "README.md")
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Workspace\n\n"
                    "This workspace contains:\n"
                    "- `software-agent-sdk/` - The main repository for "
                    "the OpenHands agent SDK\n"
                )

            logger.info(f"Cloned software-agent-sdk to: {repo_dir}")

        except subprocess.TimeoutExpired as exc:
            message = "Git clone timed out; skipping behavior test"
            logger.warning(message)
            raise SkipTest(message) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", "ignore") if exc.stderr else ""
            details = stderr.strip() or str(exc)
            message = (
                f"Git command failed while preparing behavior test workspace: {details}"
            )
            logger.warning(message)
            raise SkipTest(message) from exc
        except Exception as exc:  # noqa: BLE001
            message = f"Unable to prepare behavior test workspace: {exc}"
            logger.warning(message)
            raise SkipTest(message) from exc

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
        repo_dir = Path(self.workspace) / "software-agent-sdk"
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
            3. Wrapped up with a concise summary once the rename was complete and
               asked users if they needed to maintain compatibility as a follow-up.

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
