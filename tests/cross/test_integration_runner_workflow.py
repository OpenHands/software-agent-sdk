"""Exercise integration workflow Python entrypoints without making LLM calls."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "integration-runner.yml"


def _runner_commands() -> list[list[str]]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    commands = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            for line in step.get("run", "").splitlines():
                command = shlex.split(line.strip().removesuffix("\\"))
                if command[:3] == ["uv", "run", "python"]:
                    commands.append(command[3:])
    assert commands, "No integration runner commands found"
    return commands


@pytest.mark.parametrize("command", _runner_commands(), ids=lambda args: " ".join(args))
def test_integration_workflow_runner_help(command: list[str]) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    result = subprocess.run(
        [sys.executable, *command, "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout
