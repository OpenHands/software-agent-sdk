"""Serializers that convert ``Message`` instances into OpenAI Responses API
``input`` items. ``Message.to_responses_dict`` delegates here.
"""

from collections.abc import Sequence
from typing import Any

from openhands.sdk.llm.message import (
    ImageContent,
    Message,
    ReasoningItemModel,
    TextContent,
)


def message_to_responses_dict(
    message: Message, *, vision_enabled: bool
) -> list[dict[str, Any]]:
    """Serialize message for OpenAI Responses (input parameter).

    Produces a list of "input" items for the Responses API:
    - system: returns [], system content is expected in 'instructions'
    - user: one 'message' item with content parts -> input_text / input_image
      (when vision enabled)
    - assistant: emits prior assistant content as input_text,
      and function_call items for tool_calls
    - tool: emits function_call_output items (one per TextContent)
      with matching call_id
    """
    match message.role:
        case "system":
            return []
        case "user":
            return _user_to_responses_items(message, vision_enabled=vision_enabled)
        case "assistant":
            return _assistant_to_responses_items(message)
        case "tool":
            return _tool_to_responses_items(message, vision_enabled=vision_enabled)
        case _:
            return []


def _user_to_responses_items(
    message: Message, *, vision_enabled: bool
) -> list[dict[str, Any]]:
    """Convert user message to Responses API format."""
    content_items = _build_user_content_items(
        message.content, vision_enabled=vision_enabled
    )
    return [
        {
            "type": "message",
            "role": "user",
            "content": content_items or [{"type": "input_text", "text": ""}],
        }
    ]


def _build_user_content_items(
    content: Sequence[TextContent | ImageContent], *, vision_enabled: bool
) -> list[dict[str, Any]]:
    """Build content items for user message (input_text and input_image)."""
    items: list[dict[str, Any]] = []
    for c in content:
        if isinstance(c, TextContent):
            items.append({"type": "input_text", "text": c.text})
        elif isinstance(c, ImageContent) and vision_enabled:
            for url in c.image_urls:
                items.append(
                    {"type": "input_image", "image_url": url, "detail": "auto"}
                )
    return items


def _assistant_to_responses_items(message: Message) -> list[dict[str, Any]]:
    """Convert assistant message to Responses API format."""
    items: list[dict[str, Any]] = []

    reasoning_item = _build_reasoning_item(message.responses_reasoning_item)
    if reasoning_item:
        items.append(reasoning_item)

    content_items = _build_assistant_content_items(message.content)
    if content_items:
        items.append({"type": "message", "role": "assistant", "content": content_items})

    if message.tool_calls:
        items.extend(tc.to_responses_dict() for tc in message.tool_calls)

    return items


def _build_reasoning_item(
    reasoning_item: ReasoningItemModel | None,
) -> dict[str, Any] | None:
    """Build reasoning item from responses_reasoning_item if present."""
    if reasoning_item is None or reasoning_item.id is None:
        return None

    item: dict[str, Any] = {
        "type": "reasoning",
        "id": reasoning_item.id,
        "summary": [
            {"type": "summary_text", "text": s} for s in (reasoning_item.summary or [])
        ],
    }

    if reasoning_item.content:
        item["content"] = [
            {"type": "reasoning_text", "text": t} for t in reasoning_item.content
        ]
    if reasoning_item.encrypted_content:
        item["encrypted_content"] = reasoning_item.encrypted_content
    if reasoning_item.status:
        item["status"] = reasoning_item.status

    return item


def _build_assistant_content_items(
    content: Sequence[TextContent | ImageContent],
) -> list[dict[str, Any]]:
    """Build output_text items from assistant content."""
    return [
        {"type": "output_text", "text": c.text}
        for c in content
        if isinstance(c, TextContent) and c.text
    ]


def _tool_to_responses_items(
    message: Message, *, vision_enabled: bool
) -> list[dict[str, Any]]:
    """Convert tool message to Responses API format (function_call_output)."""
    if message.tool_call_id is None:
        return []

    items: list[dict[str, Any]] = []
    for c in message.content:
        if isinstance(c, TextContent):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message._maybe_truncate_tool_text(c.text),
                }
            )
        elif isinstance(c, ImageContent) and vision_enabled:
            for url in c.image_urls:
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": [
                            {
                                "type": "input_image",
                                "image_url": url,
                                "detail": "auto",
                            }
                        ],
                    }
                )
    return items
