"""Metadata for bash command execution."""

import json
import re
import traceback
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger
from openhands.tools.terminal.constants import (
    CMD_OUTPUT_METADATA_PS1_REGEX,
    CMD_OUTPUT_PS1_BEGIN,
    CMD_OUTPUT_PS1_END,
)


if TYPE_CHECKING:
    from re import Match

logger = get_logger(__name__)


class _SyntheticMatch:
    """A match-like object for recovered PS1 blocks.

    When concurrent output corrupts the PS1 prompt, we extract the last
    valid JSON block. This provides a match-like interface for compatibility.
    Note: group(0) is synthesized and may not match original formatting.
    """

    def __init__(
        self, content: str, original_match: "Match[str]", nested_marker_offset: int
    ):
        self._content = content
        # nested_marker_offset: position of ###PS1JSON### in group(1)
        self._actual_start = original_match.start(1) + nested_marker_offset
        self._actual_end = original_match.end(0)

    def group(self, index: int = 0) -> str:
        if index == 0:
            begin = CMD_OUTPUT_PS1_BEGIN.strip()
            end = CMD_OUTPUT_PS1_END.strip()
            return f"{begin}\n{self._content}\n{end}"
        elif index == 1:
            return self._content
        raise IndexError(f"no such group: {index}")

    def start(self, group: int = 0) -> int:
        if group == 0:
            return self._actual_start
        elif group == 1:
            return self._actual_start + len(CMD_OUTPUT_PS1_BEGIN.strip()) + 1
        raise IndexError(f"no such group: {group}")

    def end(self, group: int = 0) -> int:
        if group == 0:
            return self._actual_end
        elif group == 1:
            return self._actual_end - len(CMD_OUTPUT_PS1_END.strip()) - 1
        raise IndexError(f"no such group: {group}")


class CmdOutputMetadata(BaseModel):
    """Additional metadata captured from PS1"""

    exit_code: int = Field(
        default=-1, description="The exit code of the last executed command."
    )
    pid: int = Field(
        default=-1, description="The process ID of the last executed command."
    )
    username: str | None = Field(
        default=None, description="The username of the current user."
    )
    hostname: str | None = Field(
        default=None, description="The hostname of the machine."
    )
    working_dir: str | None = Field(
        default=None, description="The current working directory."
    )
    py_interpreter_path: str | None = Field(
        default=None, description="The path to the current Python interpreter, if any."
    )
    prefix: str = Field(default="", description="Prefix to add to command output")
    suffix: str = Field(default="", description="Suffix to add to command output")

    @classmethod
    def to_ps1_prompt(cls) -> str:
        """Convert the required metadata into a PS1 prompt."""
        prompt = CMD_OUTPUT_PS1_BEGIN
        json_str = json.dumps(
            {
                "pid": "$!",
                "exit_code": "$?",
                "username": r"\u",
                "hostname": r"\h",
                "working_dir": r"$(pwd)",
                "py_interpreter_path": r'$(command -v python || echo "")',
            },
            indent=2,
        )
        # Make sure we escape double quotes in the JSON string
        # So that PS1 will keep them as part of the output
        prompt += json_str.replace('"', r"\"")
        prompt += CMD_OUTPUT_PS1_END + "\n"  # Ensure there's a newline at the end
        return prompt

    @classmethod
    def matches_ps1_metadata(cls, string: str) -> list[re.Match[str] | _SyntheticMatch]:
        """Find all valid PS1 metadata blocks in the string.

        Handles corruption scenarios where concurrent output (e.g., progress bars,
        spinners, or other stdout) interrupts a PS1 block, causing a nested
        ###PS1JSON### marker. In such cases, extracts the LAST valid JSON block.
        """
        matches: list[re.Match[str] | _SyntheticMatch] = []
        nested_marker = CMD_OUTPUT_PS1_BEGIN.strip()

        for match in CMD_OUTPUT_METADATA_PS1_REGEX.finditer(string):
            content = match.group(1).strip()
            try:
                json.loads(content)
                matches.append(match)
            except json.JSONDecodeError:
                # Check for nested marker (corruption recovery)
                original_content = match.group(1)
                last_marker_pos = original_content.rfind(nested_marker)
                if last_marker_pos != -1:
                    content_start = last_marker_pos + len(nested_marker)
                    if content_start < len(original_content):
                        last_block = original_content[content_start:].strip()
                        if last_block:
                            try:
                                json.loads(last_block)
                                matches.append(
                                    _SyntheticMatch(last_block, match, last_marker_pos)
                                )
                                logger.debug(
                                    f"Recovered PS1 block: {last_block[:80]}"
                                    f"{'...' if len(last_block) > 80 else ''}"
                                )
                                continue
                            except json.JSONDecodeError:
                                pass

                logger.debug(
                    f"Failed to parse PS1 metadata - Skipping: [{content[:200]}"
                    f"{'...' if len(content) > 200 else ''}]" + traceback.format_exc()
                )
        return matches

    @classmethod
    def from_ps1_match(
        cls, match: "re.Match[str] | _SyntheticMatch"
    ) -> "CmdOutputMetadata":
        """Extract the required metadata from a PS1 prompt."""
        metadata = json.loads(match.group(1))
        # Create a copy of metadata to avoid modifying the original
        processed = metadata.copy()
        # Convert numeric fields
        if "pid" in metadata:
            try:
                processed["pid"] = int(float(str(metadata["pid"])))
            except (ValueError, TypeError):
                processed["pid"] = -1
        if "exit_code" in metadata:
            try:
                processed["exit_code"] = int(float(str(metadata["exit_code"])))
            except (ValueError, TypeError):
                logger.debug(
                    f"Failed to parse exit code: {metadata['exit_code']}. "
                    f"Setting to -1."
                )
                processed["exit_code"] = -1
        return cls(**processed)
