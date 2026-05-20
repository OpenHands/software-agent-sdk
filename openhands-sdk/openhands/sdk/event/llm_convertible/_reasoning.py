from rich.text import Text

from openhands.sdk.llm import ReasoningItemModel


def append_visible_responses_reasoning(
    content: Text,
    reasoning_item: ReasoningItemModel | None,
    *,
    prefix: str = "",
) -> None:
    """Append Responses API reasoning only when plaintext content exists."""
    if reasoning_item is None:
        return

    summary_lines = [summary for summary in reasoning_item.summary if summary.strip()]
    content_lines = [block for block in (reasoning_item.content or []) if block.strip()]
    if not summary_lines and not content_lines:
        return

    if prefix:
        content.append(prefix)
    content.append("Reasoning:\n", style="bold")
    for summary in summary_lines:
        content.append(f"- {summary}\n")
    for block in content_lines:
        content.append(f"{block}\n")
