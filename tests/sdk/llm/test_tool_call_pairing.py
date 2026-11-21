"""Tests for assistant tool-call / tool-result pairing enforcement."""

from openhands.sdk.llm._tool_call_pairing import ensure_tool_call_pairing
from openhands.sdk.llm.message import Message, MessageToolCall, TextContent


def _call(call_id: str, name: str = "terminal") -> MessageToolCall:
    return MessageToolCall(id=call_id, name=name, arguments="{}", origin="completion")


def _assistant(*call_ids: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="working")],
        tool_calls=[_call(cid) for cid in call_ids],
    )


def _result(call_id: str, text: str = "ok") -> Message:
    return Message(
        role="tool",
        content=[TextContent(text=text)],
        tool_call_id=call_id,
        name="terminal",
    )


def _user(text: str) -> Message:
    return Message(role="user", content=[TextContent(text=text)])


def _pairs(messages: list[Message]) -> list[tuple[str, tuple[str, ...]]]:
    """Compact (role, ids) view for assertions."""
    out = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            out.append(("assistant", tuple(c.id for c in m.tool_calls)))
        elif m.role == "tool":
            out.append(("tool", (m.tool_call_id,)))
        else:
            out.append((m.role, ()))
    return out


def test_well_formed_history_is_unchanged():
    messages = [_user("hi"), _assistant("a"), _result("a"), _user("thanks")]
    assert _pairs(ensure_tool_call_pairing(messages)) == _pairs(messages)


def test_result_separated_by_user_message_is_pulled_forward():
    # The interrupt scenario: assistant(toolUse) -> user(text) -> tool(result).
    messages = [_assistant("a"), _user("do this next"), _result("a")]
    out = ensure_tool_call_pairing(messages)
    assert _pairs(out) == [
        ("assistant", ("a",)),
        ("tool", ("a",)),
        ("user", ()),
    ]


def test_missing_result_is_synthesized():
    messages = [_assistant("a"), _user("try again")]
    out = ensure_tool_call_pairing(messages)
    assert _pairs(out) == [
        ("assistant", ("a",)),
        ("tool", ("a",)),
        ("user", ()),
    ]
    synthesized = out[1]
    assert synthesized.role == "tool"
    assert synthesized.tool_call_id == "a"
    content = synthesized.content[0]
    assert isinstance(content, TextContent)
    assert "interrupted" in content.text


def test_orphan_result_without_call_is_dropped():
    messages = [_user("hi"), _result("ghost"), _user("bye")]
    out = ensure_tool_call_pairing(messages)
    assert _pairs(out) == [("user", ()), ("user", ())]


def test_parallel_calls_each_get_their_result_in_call_order():
    # Results present but out of order and separated — reordered to call order.
    messages = [_assistant("a", "b"), _result("b"), _user("note"), _result("a")]
    out = ensure_tool_call_pairing(messages)
    assert _pairs(out) == [
        ("assistant", ("a", "b")),
        ("tool", ("a",)),
        ("tool", ("b",)),
        ("user", ()),
    ]


def test_idempotent():
    messages = [_assistant("a"), _user("x"), _result("a")]
    once = ensure_tool_call_pairing(messages)
    twice = ensure_tool_call_pairing(once)
    assert _pairs(once) == _pairs(twice)
