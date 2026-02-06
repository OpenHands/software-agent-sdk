from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.message import (
    ImageContent,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    TextContent,
)


def test_function_call_and_output_paired():
    # Assistant emits a function_call; tool returns an output for same id
    tc = MessageToolCall(
        id="abc123", name="apply_patch", arguments="{}", origin="responses"
    )
    m_assistant = Message(
        role="assistant", content=[TextContent(text="")], tool_calls=[tc]
    )
    m_tool = Message(
        role="tool",
        tool_call_id="abc123",
        name="apply_patch",
        content=[TextContent(text="done")],
    )

    llm = LLM(model="gpt-5-mini")
    _, inputs = llm.format_messages_for_responses([m_assistant, m_tool])

    # Find function_call and function_call_output
    fcs = [it for it in inputs if it.get("type") == "function_call"]
    outs = [it for it in inputs if it.get("type") == "function_call_output"]

    assert len(fcs) == 1 and len(outs) == 1
    fc = fcs[0]
    out = outs[0]
    assert fc["call_id"].startswith("fc_")
    assert out["call_id"] == fc["call_id"]


def test_system_to_responses_value_instructions_concat():
    m1 = Message(role="system", content=[TextContent(text="A"), TextContent(text="B")])
    m2 = Message(role="system", content=[TextContent(text="C")])

    # system messages become instructions string, concatenated with separators
    llm = LLM(model="gpt-5-mini")
    instr, inputs = llm.format_messages_for_responses([m1, m2])
    assert instr == "A\nB\n\n---\n\nC"
    assert inputs == []


def test_subscription_codex_transport_does_not_use_top_level_instructions_and_prepend_system_to_user():  # noqa: E501
    m_sys = Message(role="system", content=[TextContent(text="SYS")])
    m_user = Message(role="user", content=[TextContent(text="USER")])

    llm = LLM(model="gpt-5.1-codex", base_url="https://chatgpt.com/backend-api/codex")
    llm._is_subscription = True  # Mark as subscription-based
    instr, inputs = llm.format_messages_for_responses([m_sys, m_user])

    assert instr is not None
    assert "OpenHands agent" in instr
    assert len(inputs) >= 1
    first_user = next(it for it in inputs if it.get("role") == "user")
    content = first_user.get("content")
    assert isinstance(content, list)
    assert content[0]["type"] == "input_text"
    assert "SYS" in content[0]["text"]


def test_subscription_codex_transport_injects_synthetic_user_message_when_none_exists():
    m_sys = Message(role="system", content=[TextContent(text="SYS")])
    m_asst = Message(role="assistant", content=[TextContent(text="ASST")])

    llm = LLM(model="gpt-5.1-codex", base_url="https://chatgpt.com/backend-api/codex")
    llm._is_subscription = True  # Mark as subscription-based
    instr, inputs = llm.format_messages_for_responses([m_sys, m_asst])

    assert instr is not None
    assert "OpenHands agent" in instr
    assert len(inputs) >= 1
    first = inputs[0]
    assert first.get("role") == "user"
    assert "SYS" in first["content"][0]["text"]


def test_api_codex_models_keep_system_as_instructions():
    m_sys = Message(role="system", content=[TextContent(text="SYS")])
    llm = LLM(model="gpt-5.1-codex")
    instr, inputs = llm.format_messages_for_responses([m_sys])

    assert instr == "SYS"
    assert inputs == []


def test_user_to_responses_dict_with_and_without_vision():
    m = Message(
        role="user",
        content=[
            TextContent(text="hello"),
            ImageContent(image_urls=["http://x/y.png"]),
        ],
    )

    # without vision: only input_text
    items = m.to_responses_dict(vision_enabled=False)
    assert len(items) == 1 and items[0]["type"] == "message"
    content = items[0]["content"]
    assert {c["type"] for c in content} == {"input_text"}

    # with vision: include input_image
    items_v = m.to_responses_dict(vision_enabled=True)
    types = [c["type"] for c in items_v[0]["content"]]
    assert "input_text" in types and "input_image" in types


assistant_text = "Here is the result"


def test_assistant_to_responses_dict_with_text_and_tool_calls():
    # assistant prior text becomes output_text in message item
    tc = MessageToolCall(id="123", name="foo", arguments="{}", origin="responses")
    m = Message(
        role="assistant", content=[TextContent(text=assistant_text)], tool_calls=[tc]
    )

    out = m.to_responses_dict(vision_enabled=False)
    # Should include a message item with output_text, then function_call item
    assert any(item["type"] == "message" for item in out)
    msg_item = next(item for item in out if item["type"] == "message")
    assert msg_item["role"] == "assistant"
    assert {p["type"] for p in msg_item["content"]} == {"output_text"}

    fc_items = [item for item in out if item["type"] == "function_call"]
    assert len(fc_items) == 1
    assert fc_items[0]["id"].startswith("fc_") and fc_items[0]["call_id"].startswith(
        "fc_"
    )


def test_tool_to_responses_emits_function_call_output_with_fc_prefix():
    # tool result requires tool_call_id and outputs function_call_output entries
    m = Message(
        role="tool",
        tool_call_id="abc",
        name="foo",
        content=[TextContent(text="result1"), TextContent(text="result2")],
    )
    out = m.to_responses_dict(vision_enabled=False)
    assert all(item["type"] == "function_call_output" for item in out)
    assert all(item["call_id"].startswith("fc_") for item in out)


def test_tool_to_responses_truncates_output_over_limit():
    from unittest.mock import patch

    from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT

    long_text = "A" * (DEFAULT_TEXT_CONTENT_LIMIT + 1000)
    m = Message(
        role="tool",
        tool_call_id="abc",
        name="foo",
        content=[TextContent(text=long_text)],
    )

    with patch("openhands.sdk.llm.message.logger") as mock_logger:
        out = m.to_responses_dict(vision_enabled=False)

        mock_logger.warning.assert_called_once()
        assert out[0]["type"] == "function_call_output"
        assert len(out[0]["output"]) == DEFAULT_TEXT_CONTENT_LIMIT
        assert "<response clipped>" in out[0]["output"]


def test_assistant_includes_reasoning_passthrough():
    ri = ReasoningItemModel(
        id="rid1",
        summary=["s1", "s2"],
        content=["c1"],
        encrypted_content="enc",
        status="completed",
    )
    m = Message(role="assistant", content=[], responses_reasoning_item=ri)
    out = m.to_responses_dict(vision_enabled=False)

    # Contains a reasoning item with exact passthrough fields
    r_items = [it for it in out if it["type"] == "reasoning"]
    assert len(r_items) == 1
    r = r_items[0]
    assert r["id"] == "rid1"
    assert [s["text"] for s in r["summary"]] == ["s1", "s2"]
    assert [c["text"] for c in r.get("content", [])] == ["c1"]
    assert r.get("encrypted_content") == "enc"
    assert r.get("status") == "completed"
