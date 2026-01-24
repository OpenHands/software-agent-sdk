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

    When ASCII art corrupts the PS1 output, we need to extract the last
    valid JSON block. This class provides a match-like interface so the
    rest of the code can work with it transparently.
    """

    def __init__(self, content: str, original_match: "Match[str]"):
        self._content = content
        self._original_match = original_match

    def group(self, index: int = 0) -> str:
        if index == 0:
            # Full match - return PS1JSON markers + content + PS1END
            return (
                f"{CMD_OUTPUT_PS1_BEGIN.strip()}\n"
                f"{self._content}\n"
                f"{CMD_OUTPUT_PS1_END.strip()}"
            )
        elif index == 1:
            # Group 1 - the JSON content
            return self._content
        raise IndexError(f"no such group: {index}")

    def start(self, group: int = 0) -> int:
        return self._original_match.start(group)

    def end(self, group: int = 0) -> int:
        return self._original_match.end(group)


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
    def matches_ps1_metadata(cls, string: str) -> list[re.Match[str]]:
        """Find all valid PS1 metadata blocks in the string.

        Handles corruption scenarios where ASCII art or command output
        interrupts a PS1 block, causing a nested ###PS1JSON### marker.
        In such cases, we extract the LAST valid JSON block before each
        ###PS1END### marker.
        """
        matches = []
        for match in CMD_OUTPUT_METADATA_PS1_REGEX.finditer(string):
            content = match.group(1).strip()
            try:
                json.loads(content)  # Try to parse as JSON
                matches.append(match)
            except json.JSONDecodeError:
                # Check if there's a nested ###PS1JSON### marker inside
                # This happens when the first PS1 block gets corrupted by
                # command output (e.g., grunt's ASCII cat art)
                nested_marker = CMD_OUTPUT_PS1_BEGIN.strip()
                if nested_marker in content:
                    # Find the LAST occurrence of the marker
                    last_marker_pos = content.rfind(nested_marker)
                    if last_marker_pos != -1:
                        # Extract content after the last marker
                        last_block_content = content[
                            last_marker_pos + len(nested_marker) :
                        ].strip()
                        try:
                            json.loads(last_block_content)
                            # Create a synthetic match-like object
                            matches.append(_SyntheticMatch(last_block_content, match))
                            logger.debug(
                                "Recovered valid PS1 block from corrupted "
                                f"output: {last_block_content[:80]}..."
                            )
                            continue
                        except json.JSONDecodeError:
                            pass  # Fall through to the debug log below

                logger.debug(
                    f"Failed to parse PS1 metadata - Skipping: [{content[:200]}...]"
                    + traceback.format_exc()
                )
                continue  # Skip if not valid JSON
        return matches

    @classmethod
    def from_ps1_match(cls, match: re.Match[str]) -> "CmdOutputMetadata":
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
