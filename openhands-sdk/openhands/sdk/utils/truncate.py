"""Utility functions for truncating text content."""

# Default truncation limits
DEFAULT_TEXT_CONTENT_LIMIT = 50_000

# Default truncation notice
DEFAULT_TRUNCATE_NOTICE = (
    "<response clipped><NOTE>Due to the max output limit, only part of the full "
    "response has been shown to you.</NOTE>"
)


def maybe_truncate(
    content: str,
    truncate_after: int | None = None,
    truncate_notice: str = DEFAULT_TRUNCATE_NOTICE,
) -> str:
    """
    Truncate the middle of content if it exceeds the specified length.

    Keeps the head and tail of the content to preserve context at both ends.

    Args:
        content: The text content to potentially truncate
        truncate_after: Maximum length before truncation. If None, no truncation occurs
        truncate_notice: Notice to insert in the middle when content is truncated

    Returns:
        Original content if under limit, or truncated content with head and tail
        preserved
    """
    if not truncate_after or len(content) <= truncate_after or truncate_after < 0:
        return content

    # Calculate how much space we have for actual content
    available_chars = truncate_after - len(truncate_notice)
    half = available_chars // 2

    # Give extra character to head if odd number
    head_chars = half + (available_chars % 2)
    tail_chars = half

    # Keep head and tail, insert notice in the middle
    return content[:head_chars] + truncate_notice + content[-tail_chars:]
