from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.message import (
    ImageContent,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    TextContent,
)


def test_system_to_responses_value_instructions_concat():
    m1 = Message(role="system", content=[TextContent(text="A"), TextContent(text="B")])
    m2 = Message(role="system", content=[TextContent(text="C")])

    # system messages become instructions string, concatenated with separators
    llm = LLM(model="gpt-5-mini")
    instr, inputs = llm.format_messages_for_responses([m1, m2])
    assert instr == "A\nB\n\n---\n\nC"
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


def test_assistant_includes_reasoning_passthrough():
    ri = ReasoningItemModel(
        id="rid1",
        summary=["s1", "s2"],
        content=["c1"],
        encrypted_content="enc",
        status="completed",
    )
    m = Message(
        role="assistant",
        content=[TextContent(text="x")],
        responses_reasoning_item=ri,
        responses_message_id="msg_1",
        responses_message_status="completed",
    )
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


def test_assistant_from_responses_output_captures_message_id_and_status():
    msg = {
        "type": "message",
        "role": "assistant",
        "id": "msg_test_1",
        "status": "completed",
        "content": [
            {"type": "output_text", "text": "hello", "annotations": []},
        ],
    }

    from openai.types.responses.response_output_message import ResponseOutputMessage

    m = Message.from_llm_responses_output([ResponseOutputMessage.model_validate(msg)])
    assert m.responses_message_id == "msg_test_1"
    assert m.responses_message_status == "completed"
    assert [c.text for c in m.content if isinstance(c, TextContent)] == ["hello"]
