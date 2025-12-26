"""Shared utilities for behavior integration tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent
from typing import Any

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest
from tests.integration.early_stopper import EarlyStopperBase


logger = get_logger(__name__)

PINNED_SOFTWARE_AGENT_SDK_COMMIT = "693c32618dca43e6506a785da4e37575e387a638"


def clone_pinned_software_agent_repo(workspace: str) -> Path:
    """Clone the software-agent-sdk repository at a pinned commit."""
    repo_dir = Path(workspace) / "software-agent-sdk"

    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "https://github.com/OpenHands/software-agent-sdk.git",
                str(repo_dir),
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
                PINNED_SOFTWARE_AGENT_SDK_COMMIT,
                "--depth",
                "1",
            ],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            timeout=60,
        )

        subprocess.run(
            ["git", "checkout", PINNED_SOFTWARE_AGENT_SDK_COMMIT],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            timeout=30,
        )

        logger.info("Cloned software-agent-sdk to: %s", repo_dir)

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

    return repo_dir


def default_behavior_tools() -> list[Tool]:
    """Register and return the default tools for behavior tests."""
    register_tool("TerminalTool", TerminalTool)
    register_tool("FileEditorTool", FileEditorTool)
    return [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]


ENVIRONMENT_TIPS_BODY = """\
- If you see another checkout lives under
  /home/runner/_work/software-agent-sdk/software-agent-sdk,
  ignore it and stay within this workspace.
- Use `uv` (as per development guide) to avoid collision with the other checkout
  when running Python commands.
"""


def append_environment_tips(body: str) -> str:
    """Append shared environment tips to an instruction body."""
    trimmed_body = body.rstrip()
    tips = dedent(ENVIRONMENT_TIPS_BODY).rstrip()
    return f"{trimmed_body}\n\nImportant environment notes:\n{tips}\n"


class SoftwareAgentSDKBehaviorTest(BaseIntegrationTest):
    """Base class providing common setup and tools for behavior tests."""

    repo_dir: Path | None

    def __init__(
        self,
        instruction: str,
        llm_config: dict[str, Any],
        instance_id: str,
        workspace: str,
    ):
        super().__init__(instruction, llm_config, instance_id, workspace)
        self.repo_dir = None

    @property
    def tools(self) -> list[Tool]:
        return default_behavior_tools()

    def get_early_stopper(self) -> EarlyStopperBase | None:
        """Override in subclasses to provide an early stopper for this test.

        Returns:
            An EarlyStopperBase instance, or None to disable early stopping.
        """
        return None

    def setup(self) -> None:
        self.repo_dir = clone_pinned_software_agent_repo(self.workspace)
        # Configure early stopper if provided by subclass
        self.early_stopper = self.get_early_stopper()
        self.after_workspace_setup()

    def after_workspace_setup(self) -> None:
        """Hook for subclasses to perform additional setup if needed."""
        return
