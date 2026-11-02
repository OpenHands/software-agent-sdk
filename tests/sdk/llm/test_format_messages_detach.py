"""Tests for the prompt-cache-aware message detachment in LLM formatting.

``_prepare_chat_messages`` only needs to deep-copy the input when prompt caching
is active (that pass mutates content items in place). When caching is inactive
the input can be passed through, which avoids deep-copying the whole history on
every token-count and formatting call.
"""

from openhands.sdk.llm import (
    LLM,
    ImageContent,
    Message,
    MessageToolCall,
    TextContent,
)


def _messages() -> list[Message]:
    return [
        Message(
            role="system",
            content=[TextContent(text="static prompt"), TextContent(text="dynamic")],
        ),
        Message(role="user", content=[TextContent(text="hello")]),
    ]


def _rich_messages() -> list[Message]:
    """Messages exercising tool calls and image content."""
    return [
        Message(role="system", content=[TextContent(text="static prompt")]),
        Message(
            role="assistant",
            content=[TextContent(text="calling a tool")],
            tool_calls=[
                MessageToolCall(
                    id="tc1", name="terminal", arguments="{}", origin="completion"
                )
            ],
        ),
        Message(
            role="tool",
            content=[TextContent(text="tool output")],
            tool_call_id="tc1",
            name="terminal",
        ),
        Message(
            role="user",
            content=[
                TextContent(text="here is an image"),
                ImageContent(image_urls=["data:image/png;base64,iVBORw0KGgo="]),
            ],
        ),
    ]


def _snapshot(messages: list[Message]) -> list[dict]:
    return [message.model_dump() for message in messages]


def test_prepare_returns_input_when_caching_inactive():
    llm = LLM(model="gpt-4o", api_key="test")
    assert not llm.is_caching_prompt_active()

    messages = _messages()
    prepared = llm._prepare_chat_messages(messages)

    # Same objects: no deep copy was taken.
    assert prepared is messages
    assert all(a is b for a, b in zip(prepared, messages))


def test_prepare_detaches_when_caching_active():
    llm = LLM(model="claude-sonnet-4-5", api_key="test")
    assert llm.is_caching_prompt_active()

    messages = _messages()
    prepared = llm._prepare_chat_messages(messages)

    # A detached copy is made so the in-place cache markers do not leak into the
    # caller's messages.
    assert prepared is not messages
    assert all(a is not b for a, b in zip(prepared, messages))
    assert all(item.cache_prompt is False for item in messages[0].content)
    assert prepared[0].content[0].cache_prompt is True


def test_format_does_not_mutate_input_without_caching():
    llm = LLM(model="gpt-4o", api_key="test")
    messages = _messages()
    before = [item.model_dump() for item in messages[0].content]

    llm.format_messages_for_llm(messages)

    assert [item.model_dump() for item in messages[0].content] == before
    assert all(item.cache_prompt is False for item in messages[0].content)


def test_rich_messages_not_mutated_without_caching():
    llm = LLM(model="gpt-4o", api_key="test")
    assert not llm.is_caching_prompt_active()
    messages = _rich_messages()
    before = _snapshot(messages)

    llm.format_messages_for_llm(messages)

    assert _snapshot(messages) == before


def test_output_identical_to_deepcopy_path_without_caching():
    """The pass-through output must equal what the old deepcopy path produced."""
    llm = LLM(model="gpt-4o", api_key="test")
    assert not llm.is_caching_prompt_active()

    messages = _rich_messages()
    got = llm.format_messages_for_llm(messages)

    # Reference: deep-copy first, exactly as the previous implementation did.
    import copy

    reference = llm._to_chat_dicts(copy.deepcopy(messages))
    assert got == reference


def test_rich_messages_caching_active_unchanged_and_detached():
    llm = LLM(model="claude-sonnet-4-5", api_key="test")
    assert llm.is_caching_prompt_active()
    messages = _rich_messages()
    before = _snapshot(messages)

    formatted = llm.format_messages_for_llm(messages)

    # Caller's messages are untouched (cache markers live on the detached copy).
    assert _snapshot(messages) == before
    # Cache markers are applied to the system message on the copy.
    assert formatted[0]["content"][0].get("cache_control") == {"type": "ephemeral"}
