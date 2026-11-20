"""Utility functions for truncating text content."""

import hashlib
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Default truncation limits
DEFAULT_TEXT_CONTENT_LIMIT = 50_000

# Default truncation notice
DEFAULT_TRUNCATE_NOTICE = (
    "<response clipped><NOTE>Due to the max output limit, only part of the full "
    "response has been shown to you.</NOTE>"
)  # 113 chars

DEFAULT_TRUNCATE_NOTICE_WITH_PERSIST = (
    "<response clipped><NOTE>Due to the max output limit, only part of the full "
    "response has been shown to you. The complete output has been saved to "
    "{file_path} - you can use other tools to view the full content (truncated "
    "part starts around line {line_num}).</NOTE>"
)


class TruncationMetadata(BaseModel):
    """Machine-readable accounting for inline output truncation and spillover."""

    truncated: bool = Field(
        default=False,
        description="Whether inline output was clipped.",
    )
    original_chars: int = Field(
        default=0,
        ge=0,
        description="Total character length prior to truncation.",
    )
    original_lines: int = Field(
        default=0,
        ge=0,
        description="Total line count prior to truncation.",
    )
    kept_chars: int = Field(
        default=0,
        ge=0,
        description="Characters from the original output retained inline.",
    )
    omitted_chars: int = Field(
        default=0,
        ge=0,
        description="Characters omitted from the middle of the inline output.",
    )
    head_end_line: int | None = Field(
        default=None,
        description="1-based line number where the retained head segment ends.",
    )
    tail_start_line: int | None = Field(
        default=None,
        description="1-based line number where the retained tail segment begins.",
    )
    spillover_path: str | None = Field(
        default=None,
        description="Filesystem path holding the complete unclipped output, if saved.",
    )


class TruncatedTextResult(NamedTuple):
    """Result of ``truncate_with_metadata``."""

    text: str
    metadata: TruncationMetadata


def _save_full_content(content: str, save_dir: str, tool_prefix: str) -> str | None:
    """Save full content to the specified directory and return the file path."""

    save_dir_path = Path(save_dir)

    # Generate hash-based filename for deduplication
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    filename = f"{tool_prefix}_output_{content_hash}.txt"
    file_path = save_dir_path / filename

    # Only write if file doesn't exist (deduplication)
    try:
        save_dir_path.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.debug(f"Failed to save full content to {file_path}: {e}")
        return None

    return str(file_path)


def truncate_with_metadata(
    content: str,
    truncate_after: int | None = None,
    truncate_notice: str = DEFAULT_TRUNCATE_NOTICE,
    save_dir: str | None = None,
    tool_prefix: str = "output",
) -> TruncatedTextResult:
    """Truncate ``content`` when over limit and return structured metadata."""
    original_chars = len(content)
    original_lines = len(content.splitlines())

    # 1) Early exits: no truncation requested, or content already within limit
    if not truncate_after or original_chars <= truncate_after or truncate_after < 0:
        return TruncatedTextResult(
            text=content,
            metadata=TruncationMetadata(
                truncated=False,
                original_chars=original_chars,
                original_lines=original_lines,
                kept_chars=original_chars,
                omitted_chars=0,
            ),
        )

    # 2) If even the base notice doesn't fit, return a slice of it
    if len(truncate_notice) >= truncate_after:
        return TruncatedTextResult(
            text=truncate_notice[:truncate_after],
            metadata=TruncationMetadata(
                truncated=True,
                original_chars=original_chars,
                original_lines=original_lines,
                kept_chars=0,
                omitted_chars=original_chars,
                head_end_line=0,
                tail_start_line=None,
                spillover_path=None,
            ),
        )

    # 3) Calculate proposed head size based on base notice
    # (for consistent line number calc)
    available_chars = truncate_after - len(truncate_notice)
    # Prefer giving the "extra" char to head (ceil split)
    proposed_head = available_chars // 2 + (available_chars % 2)

    # 4) Optionally save full content, then construct the final notice
    final_notice = truncate_notice
    saved_file_path: str | None = None
    if save_dir:
        saved_file_path = _save_full_content(content, save_dir, tool_prefix)
        if saved_file_path:
            head_content_lines = len(content[:proposed_head].splitlines())
            final_notice = DEFAULT_TRUNCATE_NOTICE_WITH_PERSIST.format(
                file_path=saved_file_path,
                line_num=head_content_lines + 1,  # +1 to indicate next line
            )

    # 5) If the final notice (with persist info) alone fills the
    # budget, return a slice of it
    if len(final_notice) >= truncate_after:
        return TruncatedTextResult(
            text=final_notice[:truncate_after],
            metadata=TruncationMetadata(
                truncated=True,
                original_chars=original_chars,
                original_lines=original_lines,
                kept_chars=0,
                omitted_chars=original_chars,
                head_end_line=0,
                tail_start_line=None,
                spillover_path=saved_file_path,
            ),
        )

    # 6) Allocate remaining budget to head/tail
    remaining = truncate_after - len(final_notice)
    head_chars = min(proposed_head, remaining)
    tail_chars = remaining - head_chars
    kept_chars = head_chars + tail_chars
    head_end_line = len(content[:head_chars].splitlines()) if head_chars > 0 else 0
    if tail_chars > 0:
        tail_lines = len(content[-tail_chars:].splitlines())
        tail_start_line: int | None = max(
            head_end_line + 1, original_lines - tail_lines + 1
        )
    else:
        tail_start_line = None

    truncated_text = (
        content[:head_chars]
        + final_notice
        + (content[-tail_chars:] if tail_chars > 0 else "")
    )
    return TruncatedTextResult(
        text=truncated_text,
        metadata=TruncationMetadata(
            truncated=True,
            original_chars=original_chars,
            original_lines=original_lines,
            kept_chars=kept_chars,
            omitted_chars=max(0, original_chars - kept_chars),
            head_end_line=head_end_line,
            tail_start_line=tail_start_line,
            spillover_path=saved_file_path,
        ),
    )


def maybe_truncate(
    content: str,
    truncate_after: int | None = None,
    truncate_notice: str = DEFAULT_TRUNCATE_NOTICE,
    save_dir: str | None = None,
    tool_prefix: str = "output",
) -> str:
    """
    Truncate the middle of content if it exceeds the specified length.

    Keeps the head and tail of the content to preserve context at both ends.
    Optionally saves the full content to a file for later investigation.
    If saving fails, returns clipped content without a saved-file reference.
    """
    return truncate_with_metadata(
        content=content,
        truncate_after=truncate_after,
        truncate_notice=truncate_notice,
        save_dir=save_dir,
        tool_prefix=tool_prefix,
    ).text
