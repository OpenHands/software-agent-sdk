"""Command execution for dynamic skill context injection."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

from openhands.sdk.context.skills.exceptions import SkillError
from openhands.sdk.context.skills.types import CommandSpec
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# 50KB per command
MAX_OUTPUT_SIZE: Final[int] = 50 * 1024


def _execute_command(spec: CommandSpec, working_dir: Path | None = None) -> str:
    """Execute a single command and return its output."""
    cwd = str(working_dir) if working_dir else None
    try:
        result = subprocess.run(
            spec.command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=spec.timeout,
        )
        if result.returncode != 0:
            return _handle_error(
                spec, f"Command exited with code {result.returncode}: {result.stderr}"
            )
        output = result.stdout.strip()
        if len(output) > MAX_OUTPUT_SIZE:
            output = output[:MAX_OUTPUT_SIZE] + "\n... [output truncated]"
        return output

    except subprocess.TimeoutExpired:
        return _handle_error(spec, f"Command timed out after {spec.timeout}s")
    except Exception as e:
        return _handle_error(spec, f"Failed to execute command: {e}")


def _handle_error(spec: CommandSpec, message: str) -> str:
    """Handle command execution error based on on_error setting."""
    logger.warning("Skill command '%s' failed: %s", spec.name, message)
    if spec.on_error == "fail":
        raise SkillError(message)
    if spec.on_error == "empty":
        return ""
    return f"[Error: {message}]"


def _execute_commands(
    commands: list[CommandSpec],
    working_dir: Path | None = None,
) -> dict[str, str]:
    """Execute all commands and return name->output mapping."""
    return {spec.name: _execute_command(spec, working_dir) for spec in commands}


def render_content_with_commands(
    content: str,
    commands: list[CommandSpec],
    working_dir: Path | None = None,
    extra_vars: dict[str, str] | None = None,
) -> str:
    """Execute commands and substitute {{var_name}} patterns in content."""
    if not commands and not extra_vars:
        return content

    # Execute commands
    variables = _execute_commands(commands, working_dir) if commands else {}
    if extra_vars:
        collisions = set(variables) & set(extra_vars)
        if collisions:
            logger.warning("extra_vars overriding command outputs: %s", collisions)
        variables.update(extra_vars)

    if not variables:
        return content

    # Substitute {{var_name}} patterns
    def replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name in variables:
            return variables[var_name]
        return match.group(0)

    return re.sub(r"\{\{(\w+)\}\}", replace_var, content)
