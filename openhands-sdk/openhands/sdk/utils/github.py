"""Utility functions for GitHub integrations."""

import re


# Zero-width joiner character (U+200D)
ZWJ = "\u200d"


def sanitize_openhands_mentions(text: str) -> str:
    """Sanitize @OpenHands mentions in text to prevent self-mention loops.

    This function inserts a zero-width joiner (ZWJ) after the @ symbol in
    @OpenHands mentions, making them non-clickable in GitHub comments while
    preserving readability.

    Args:
        text: The text to sanitize

    Returns:
        Text with @OpenHands mentions sanitized
        (e.g., "@OpenHands" -> "@\u200dOpenHands")

    Examples:
        >>> sanitize_openhands_mentions("Thanks @OpenHands for the help!")
        'Thanks @\\u200DOpenHands for the help!'
        >>> sanitize_openhands_mentions("Check @openhands and @OpenHands")
        'Check @\\u200Dopenhands and @\\u200DOpenHands'
    """
    # Pattern to match @OpenHands in various case combinations
    # Matches @ followed by openhands (case-insensitive) at word boundaries
    pattern = r"@(?=\b[Oo]pen[Hh]ands\b)"

    # Replace @ with @ + ZWJ for OpenHands mentions
    sanitized = re.sub(pattern, f"@{ZWJ}", text, flags=re.IGNORECASE)

    return sanitized
