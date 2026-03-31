"""Command execution for dynamic skill context injection.

Supports inline !`command` syntax in skill content. Commands are executed
at render time and their output replaces the placeholder.

Safety rules:
- Fenced (```) and inline (`) code blocks are preserved, never executed.
- An unclosed fenced block (odd number of ```) extends to EOF, protecting
  any trailing content from accidental execution.
- Use \\!`cmd` to produce the literal text !`cmd` without execution.

**Security Warning**: Commands are executed via shell with full process
privileges. Only use with trusted skill sources.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# 50KB per command output
MAX_OUTPUT_SIZE: Final[int] = 50 * 1024

# Default timeout per command in seconds
DEFAULT_TIMEOUT: Final[float] = 10.0

# Single-pass pattern: matches fenced code blocks, escaped commands, inline code,
# or !`command`.  Order matters – earlier alternatives take priority.
#
# 1. Fenced blocks (``` ... ```).  An *unclosed* fence (odd number of ```)
#    matches through to the end of the string so that content after the last
#    opening ``` is never accidentally executed.
# 2. Escaped commands (\!`...`) – the backslash is stripped and the rest is
#    kept as a literal !`...` so authors can document the syntax itself.
# 3. Inline code (`...`) not preceded by `!`.
# 4. Executable commands (!`...`).
_COMBINED_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<fenced>```[\s\S]*?(?:```|$))"  # fenced code block (unclosed → EOF)
    r"|(?P<escaped>\\!`[^`]+`)"  # escaped \!`command` → literal
    r"|(?P<inline>(?<!!)`[^`]+`)"  # inline code (not preceded by !)
    r"|!`(?P<cmd>[^`]+)`"  # !`command`
)


def _execute_inline_command(
    command: str,
    working_dir: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Execute a single inline shell command and return its output."""
    cwd = str(working_dir) if working_dir else None
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            message = (
                f"Command `{command}` exited with "
                f"code {result.returncode}: {result.stderr}"
            )
            logger.warning("Skill command failed: %s", message)
            return f"[Error: {message}]"

        output = result.stdout.strip()
        if len(output.encode()) > MAX_OUTPUT_SIZE:
            output = output.encode()[:MAX_OUTPUT_SIZE].decode("utf-8", errors="ignore")
            output += "\n... [output truncated]"
        return output

    except subprocess.TimeoutExpired:
        message = f"Command `{command}` timed out after {timeout}s"
        logger.warning("Skill command failed: %s", message)
        return f"[Error: {message}]"
    except Exception as e:
        message = f"Failed to execute command `{command}`: {e}"
        logger.warning("Skill command failed: %s", message)
        return f"[Error: {message}]"


def render_content_with_commands(
    content: str,
    working_dir: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Execute inline !`command` patterns in content and replace with output.

    Code blocks (fenced ``` and inline `) are preserved and not executed.
    Unclosed fenced blocks (odd number of ```) are treated as extending to
    EOF so that trailing content is never accidentally executed.
    Use \\!`cmd` to produce the literal text !`cmd` without execution.
    """

    def _replace(match: re.Match[str]) -> str:
        if match.group("fenced") or match.group("inline"):
            return match.group(0)
        if match.group("escaped"):
            # Strip leading backslash: \!`cmd` → !`cmd`
            return match.group("escaped")[1:]
        return _execute_inline_command(match.group("cmd"), working_dir, timeout)

    return _COMBINED_PATTERN.sub(_replace, content)
