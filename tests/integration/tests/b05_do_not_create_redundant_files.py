"""Test that the agent does not create redundant files when not asked."""

from __future__ import annotations

import os
import subprocess
from textwrap import dedent

from openhands.sdk import get_logger
from openhands.sdk.event.base import Event
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult
from tests.integration.behavior_utils import (
    get_conversation_summary,
)
from tests.integration.early_stopper import EarlyStopperBase, EarlyStopResult
from tests.integration.utils.llm_judge import judge_agent_behavior


INSTRUCTION = dedent(
    """
    In this repo there was support for training smolvla policy with custom dataset, by using the following command: lerobot-train --policy.path=lerobot/smolvla_base --dataset.repo_id=${HF_USER}/mydataset --batch_size=64 --steps=20000 --output_dir=outputs/train/my_smolvla --job_name=my_smolvla_training --policy.device=cuda --wandb.enable=true. I want to create a standalone Python-based training example in examples/tutorial/smolvla/train_smolvla_example.py, following the same format as the `using_smolvla_example.py` script in the same directory. Can you help me take a look at the codebase and relevant files carefully and help me implement that training script?
    """  # noqa: E501
)

logger = get_logger(__name__)


class RedundantFileCreationPruner(EarlyStopperBase):
    """Stop early if agent creates too many files.

    For b05 test, the agent should only create 1-2 files (the training script
    and optionally a README). Creating more indicates redundant files.
    """

    def __init__(
        self,
        max_creates: int = 2,
        allowed_patterns: list[str] | None = None,
    ):
        self.max_creates = max_creates
        # Patterns for auto-generated or framework files to ignore
        self.allowed_patterns = allowed_patterns or []

    def check(self, events: list[Event]) -> EarlyStopResult:
        """Check if too many file create operations were performed."""
        from openhands.sdk.event.llm_convertible.action import ActionEvent
        from openhands.tools.file_editor.definition import (
            FileEditorAction,
            FileEditorTool,
        )

        create_count = 0
        created_files = []
        for event in events:
            if (
                isinstance(event, ActionEvent)
                and event.tool_name == FileEditorTool.name
            ):
                if event.action is not None and isinstance(
                    event.action, FileEditorAction
                ):
                    if event.action.command == "create":
                        path = event.action.path
                        # Skip files matching allowed patterns (auto-generated, etc.)
                        if any(pattern in path for pattern in self.allowed_patterns):
                            continue
                        create_count += 1
                        created_files.append(path)

        if create_count > self.max_creates:
            return EarlyStopResult(
                should_stop=True,
                reason=(
                    f"Created {create_count} files (max {self.max_creates}): "
                    f"{', '.join(created_files[-3:])}"
                ),
            )

        return EarlyStopResult(should_stop=False)


class NoRedundantFilesTest(BaseIntegrationTest):
    """Ensure the agent does not create any redundant files (e.g., .md files)
    that are not asked by users when performing the task."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set up early stopper with patterns for auto-generated files
        self.early_stopper = RedundantFileCreationPruner(
            max_creates=3,  # Allow training script, README, and test file
            allowed_patterns=[
                ".openhands/",  # Auto-generated framework files
                "__pycache__/",  # Python cache
            ],
        )

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]

    def setup(self) -> None:  # noqa: D401
        """Set up a realistic codebase by cloning the lerobot repo."""
        try:
            # Clone the lerobot repository
            # Git clone requires the target directory to be empty or non-existent
            # The workspace is created as an empty temp directory, but git clone
            # expects to create the directory itself, so we clone to a subdirectory
            repo_dir = os.path.join(self.workspace, "lerobot")

            # Pin to specific commit on main to ensure test stability
            target_commit = "784cdae55a863b581805ca6060174fa2bae2a85a"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "https://github.com/huggingface/lerobot.git",
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
            Verify that the agent did not create any redundant files (e.g., .md files)
            that are not asked by users when performing the task.

            The agent must:
            1. Create the new file `examples/tutorial/smolvla/train_smolvla_example.py` as requested.
            2. Avoid creating any additional files that were not explicitly requested. Only one README.md file is acceptable if it pertains to the new training script.

            Did the agent follow these rules?
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
                    "Agent did not create any redundant files. "
                    f"Judge reasoning: {judgment.reasoning} "
                    f"(confidence={judgment.confidence:.2f})"
                ),
            )

        return TestResult(
            success=False,
            reason=(
                "Agent did not avoid creating redundant files. "
                f"Judge reasoning: {judgment.reasoning} "
                f"(confidence={judgment.confidence:.2f})"
            ),
        )
