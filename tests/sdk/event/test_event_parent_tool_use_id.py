"""Tests for Event.parent_tool_use_id field."""

from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent


def test_parent_tool_use_id_defaults_none_and_roundtrips():
    e = ConversationStateUpdateEvent(key="x", value=1, source="environment")
    assert e.parent_tool_use_id is None
    dumped = e.model_dump(mode="json")
    assert "parent_tool_use_id" in dumped and dumped["parent_tool_use_id"] is None

    e2 = ConversationStateUpdateEvent(
        key="x", value=1, source="environment", parent_tool_use_id="toolu_123"
    )
    restored = type(e2).model_validate_json(e2.model_dump_json())
    assert restored.parent_tool_use_id == "toolu_123"
