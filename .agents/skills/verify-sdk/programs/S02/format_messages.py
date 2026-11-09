"""Turn a finished conversation's saved events back into provider payloads,
as a program does to replay or inspect a conversation.

Usage: format_messages.py CONVERSATION_ID

Reloads every `events/event-*.json` file with `Event.model_validate_json`,
folds them into `Message`s, and formats them for Chat Completions (list and
string serializer, with and without vision, with Anthropic cache markers) and
for the Responses API.
"""

import json
import sys

from pydantic import SecretStr
from sdk_verify import conversation_dir, conversation_record, emit, import_tools

from openhands.sdk import LLM
from openhands.sdk.event import Event, LLMConvertibleEvent
from openhands.sdk.llm import ImageContent, Message, TextContent


cid = sys.argv[1]
import_tools(conversation_record(cid)["tools"])
files = sorted((conversation_dir(cid) / "events").glob("event-*.json"))
events = [Event.model_validate_json(f.read_text()) for f in files]
messages = LLMConvertibleEvent.events_to_messages(
    [e for e in events if isinstance(e, LLMConvertibleEvent)]
)
# A user turn with an image, as a program would append before replaying.
messages.append(
    Message(
        role="user",
        content=[
            TextContent(text="And this picture?"),
            ImageContent(image_urls=["https://example.com/cat.png"]),
        ],
    )
)


def llm(model: str, **kw) -> LLM:
    return LLM(model=model, api_key=SecretStr("sk-test"), usage_id="format", **kw)


chat = llm("openai/gpt-4o-mini").format_messages_for_llm(messages)
as_string = llm(
    "openai/gpt-4o-mini", force_string_serializer=True
).format_messages_for_llm(messages)
no_vision = llm("openai/gpt-4o-mini", disable_vision=True).format_messages_for_llm(
    messages
)
anthropic = llm("anthropic/claude-sonnet-4-5-20250929").format_messages_for_llm(
    messages
)
instructions, items = llm("openai/gpt-5-mini").format_messages_for_responses(messages)


def has_image(payload: list[dict]) -> bool:
    return "image_url" in json.dumps(payload)


emit(
    {
        "ok": True,
        "event_files": len(files),
        "message_roles": [m.role for m in messages],
        "chat_roles": [m["role"] for m in chat],
        "chat_has_tool_messages": any(m["role"] == "tool" for m in chat),
        "chat_list_content": isinstance(chat[0]["content"], list),
        # The string serializer keeps a list only where an image would be lost.
        "string_content_text_turns": all(
            isinstance(m.get("content"), str | type(None)) for m in as_string[:-1]
        ),
        "string_content_image_turn": type(as_string[-1].get("content")).__name__,
        "vision_image_sent": has_image(chat),
        "no_vision_image_sent": has_image(no_vision),
        "anthropic_cache_markers": json.dumps(anthropic).count("cache_control"),
        "openai_cache_markers": json.dumps(chat).count("cache_control"),
        "responses_instructions": bool(instructions),
        "responses_item_types": sorted({i.get("type", "message") for i in items}),
    }
)
