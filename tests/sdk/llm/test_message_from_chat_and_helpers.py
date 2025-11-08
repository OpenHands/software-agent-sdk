from types import SimpleNamespace

import pytest

from openhands.sdk.llm.message import Message, TextContent, content_to_str


def test_from_llm_chat_message_raises_when_only_non_function_tool_calls():
    # tool_calls with one non-function entry should raise ValueError
    non_function_call = SimpleNamespace(type="non_function")
    # Use a lightweight stub instead of LiteLLMMessage to allow non-function tool_calls
    m = SimpleNamespace(role="assistant", content="hi", tool_calls=[non_function_call])
    with pytest.raises(ValueError, match="none are of type 'function'"):
        Message.from_llm_chat_message(m)  # type: ignore[arg-type]


def test_coerce_content_validator_handles_none_and_string():
    # content=None coerces to [] via model_validate
    msg_none = Message.model_validate({"role": "user", "content": None})
    assert msg_none.content == []

    # content as string coerces to [TextContent] via model_validate
    msg_str = Message.model_validate({"role": "user", "content": "hello"})
    assert len(msg_str.content) == 1
    assert isinstance(msg_str.content[0], TextContent)
    assert msg_str.content[0].text == "hello"


def test_content_to_str_helper():
    parts = content_to_str([TextContent(text="a"), TextContent(text="b")])
    assert parts == ["a", "b"]


def test_to_responses_value_system_direct():
    # Direct test for system instructions via to_responses_value
    m = Message(role="system", content=[TextContent(text="A"), TextContent(text="B")])
    val = m.to_responses_value(vision_enabled=False)
    assert val == "A\nB"
