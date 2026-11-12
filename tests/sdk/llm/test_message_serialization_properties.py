"""Property tests for serialising messages for the LLM APIs.

``Message.to_chat_dict`` never mutates the message, never emits an empty
content list or a blank text block, and keeps every image when vision is
enabled. A tool result's text and images reach the model identically through
Chat Completions and through the Responses API.
"""

from typing import Any, Literal

from hypothesis import given, strategies as st

from openhands.sdk.llm.message import (
    ImageContent,
    Message,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT


ROLES: list[Literal["user", "system", "assistant", "tool"]] = [
    "user",
    "system",
    "assistant",
    "tool",
]

images = st.builds(
    ImageContent,
    image_urls=st.lists(
        st.sampled_from(["https://example.com/a.png", "https://example.com/b.png"]),
        min_size=1,
        max_size=2,
    ),
)
texts = st.builds(
    TextContent,
    text=st.sampled_from(["", " ", "\n\t", "hi"]) | st.text(max_size=6),
    cache_prompt=st.booleans(),
)
tool_result_texts = (
    st.text(min_size=1, max_size=20)
    | st.integers(
        min_value=DEFAULT_TEXT_CONTENT_LIMIT - 2,
        max_value=DEFAULT_TEXT_CONTENT_LIMIT + 64,
    ).map(lambda length: "A" * length)
).map(lambda text: TextContent(text=text))


@st.composite
def messages(draw: st.DrawFn) -> Message:
    content = draw(st.lists(texts | images, max_size=4))
    role = draw(st.sampled_from(ROLES))
    if role == "tool":
        return Message(role=role, content=content, tool_call_id="call_1", name="t")
    if role == "assistant" and draw(st.booleans()):
        tool_call = MessageToolCall(
            id="call_1", name="t", arguments="{}", origin="completion"
        )
        return Message(role=role, content=content, tool_calls=[tool_call])
    return Message(role=role, content=content)


chat_options = st.fixed_dictionaries(
    {
        name: st.booleans()
        for name in (
            "cache_enabled",
            "vision_enabled",
            "function_calling_enabled",
            "force_string_serializer",
            "send_reasoning_content",
        )
    }
)


def _content_parts(chat_message: dict[str, Any]) -> list[dict[str, Any]]:
    content = chat_message.get("content")
    return content if isinstance(content, list) else []


@given(message=messages(), options=chat_options)
def test_chat_dict_has_no_empty_content_list_or_blank_text(
    message: Message, options: dict[str, bool]
):
    chat_message = message.to_chat_dict(**options)

    assert chat_message.get("content") != []
    assert all(
        part["text"].strip()
        for part in _content_parts(chat_message)
        if part["type"] == "text"
    )


@given(
    message=messages(),
    options=chat_options.map(lambda options: {**options, "vision_enabled": True}),
)
def test_chat_dict_keeps_every_image_when_vision_is_enabled(
    message: Message, options: dict[str, bool]
):
    chat_message = message.to_chat_dict(**options)

    assert [
        part["image_url"]["url"]
        for part in _content_parts(chat_message)
        if part["type"] == "image_url"
    ] == [
        url
        for item in message.content
        if isinstance(item, ImageContent)
        for url in item.image_urls
    ]


@given(message=messages(), options=chat_options)
def test_chat_dict_does_not_mutate_the_message(
    message: Message, options: dict[str, bool]
):
    stored = message.model_dump_json()

    message.to_chat_dict(**options)

    assert message.model_dump_json() == stored


@given(
    content=st.lists(tool_result_texts | images, min_size=1, max_size=3),
    vision_enabled=st.booleans(),
)
def test_tool_result_reaches_chat_and_responses_identically(
    content: list[TextContent | ImageContent], vision_enabled: bool
):
    message = Message(role="tool", tool_call_id="call_1", name="t", content=content)

    chat_parts = _content_parts(
        message.to_chat_dict(
            cache_enabled=False,
            vision_enabled=vision_enabled,
            function_calling_enabled=True,
            force_string_serializer=False,
            send_reasoning_content=False,
        )
    )
    outputs = [
        item["output"]
        for item in message.to_responses_dict(vision_enabled=vision_enabled)
        if item["type"] == "function_call_output"
    ]

    assert [
        part["text"]
        for part in chat_parts
        if part["type"] == "text" and part["text"].strip()
    ] == [output for output in outputs if isinstance(output, str) and output.strip()]
    assert [
        part["image_url"]["url"] for part in chat_parts if part["type"] == "image_url"
    ] == [
        part["image_url"]
        for output in outputs
        if isinstance(output, list)
        for part in output
        if part["type"] == "input_image"
    ]
