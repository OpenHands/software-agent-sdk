from rich.text import Text

from openhands.sdk.llm import ReasoningItemModel


def _get_visible_responses_reasoning_lines(
    reasoning_item: ReasoningItemModel | None,
) -> tuple[list[str], list[str]] | None:
    if reasoning_item is None:
        return None

    summary_lines = [summary for summary in reasoning_item.summary if summary.strip()]
    content_lines = [block for block in (reasoning_item.content or []) if block.strip()]
    if not summary_lines and not content_lines:
        return None

    return summary_lines, content_lines


def has_visible_responses_reasoning(
    reasoning_item: ReasoningItemModel | None,
) -> bool:
    """Return whether a Responses reasoning item has visible plaintext."""
    return _get_visible_responses_reasoning_lines(reasoning_item) is not None


def append_visible_responses_reasoning(
    content: Text,
    reasoning_item: ReasoningItemModel | None,
) -> None:
    """Append Responses API reasoning only when plaintext content exists."""
    visible_lines = _get_visible_responses_reasoning_lines(reasoning_item)
    if visible_lines is None:
        return

    summary_lines, content_lines = visible_lines
    content.append("Reasoning:\n", style="bold")
    for summary in summary_lines:
        content.append(f"- {summary}\n")
    for block in content_lines:
        content.append(f"{block}\n")
