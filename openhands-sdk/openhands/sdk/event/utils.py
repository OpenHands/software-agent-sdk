from rich.text import Text

from openhands.sdk.llm import ReasoningItemModel


def render_responses_reasoning_block(
    reasoning_item: ReasoningItemModel | None,
    *,
    leading_newlines: bool = False,
) -> Text | None:
    """Build a Rich Text block for Responses API reasoning.

    Only renders when either summary or content is present. Returns None
    when there is nothing to render.
    """
    if reasoning_item is None:
        return None

    has_summary = bool(reasoning_item.summary)
    has_content = bool(reasoning_item.content)
    if not (has_summary or has_content):
        return None

    t = Text()
    if leading_newlines:
        t.append("\n\n")
    t.append("Reasoning:\n", style="bold")

    if has_summary:
        for s in list(reasoning_item.summary or []):
            t.append(f"- {s}\n")

    if has_content:
        for b in list(reasoning_item.content or []):
            t.append(f"{b}\n")

    return t
