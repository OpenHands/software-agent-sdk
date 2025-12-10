"""Test that agent updates terminal truncation limit without broad test runs."""

from __future__ import annotations

import os
import subprocess
from textwrap import dedent

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult
from tests.integration.behavior_utils import (
    get_conversation_summary,
)
from tests.integration.utils.llm_judge import judge_agent_behavior


INSTRUCTION = dedent(
    """
    I want to adjust the terminal tool truncation limit, i.e. reducing `MAX_CMD_OUTPUT_SIZE` to 20_000. Can you help with that? Also adjust corresponding tests to verify the change if relevant.
    """  # noqa: E501
)

logger = get_logger(__name__)


class NoOververificationTest(BaseIntegrationTest):
    """Ensure the agent updates truncation limit with scoped verification."""

    INSTRUCTION: str = INSTRUCTION

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]

    def setup(self) -> None:  # noqa: D401
        """Set up a realistic codebase by cloning the software-agent-sdk repo."""
        try:
            # Clone the software-agent-sdk repository
            # Git clone requires the target directory to be empty or non-existent
            # The workspace is created as an empty temp directory, but git clone
            # expects to create the directory itself, so we clone to a subdirectory
            repo_dir = os.path.join(self.workspace, "software-agent-sdk")

            # Pin to specific commit on main to ensure test stability
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

            # Fetch and checkout the pinned commit
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

            # Update the working directory context
            # Note: The agent will see files in workspace, so we inform
            # them about the repo
            readme_path = os.path.join(self.workspace, "README.md")
            with open(readme_path, "w") as f:
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
        except Exception as exc:
            message = f"Unable to prepare behavior test workspace: {exc}"
            logger.warning(message)
            raise SkipTest(message) from exc

    def verify_result(self) -> TestResult:
        conversation_summary = get_conversation_summary(self.collected_events)
        evaluation_criteria = dedent(
            """
            Verify that the agent did not over-verify the truncation limit change
            by running test suites broader than necessary if not asked or required.

            The agent must:
            1. Update `MAX_CMD_OUTPUT_SIZE` to 20_000.
            2. Optionally execute only the targeted pytest command for files under `tests/tools`.
            3. Avoid running broader pytest suites or unrelated packages and explain the focused verification.
            4. Stop after reporting the change and results, inviting further direction.

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
