"""Tests for Event.parent_tool_use_id serialization, compatibility, and masking."""

import json
from uuid import uuid4

from openhands.sdk import LLM, Agent, Event, LocalConversation, Message, TextContent
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent


def test_parent_tool_use_id_defaults_none_and_omitted_with_exclude_none():
    event = ConversationStateUpdateEvent(key="x", value=1, source="environment")
    assert event.parent_tool_use_id is None

    wire_payload = event.model_dump(mode="json", exclude_none=True)
    assert "parent_tool_use_id" not in wire_payload

    restored = Event.model_validate(event.model_dump(mode="json"))
    assert restored.parent_tool_use_id is None


def test_parent_tool_use_id_round_trips_when_set():
    event = MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text="working")]),
        parent_tool_use_id="toolu_subagent_01",
    )
    assert event.parent_tool_use_id == "toolu_subagent_01"

    raw_json = event.model_dump_json()
    restored = Event.model_validate_json(raw_json)
    assert restored.parent_tool_use_id == "toolu_subagent_01"

    wire_payload = restored.model_dump(mode="json", exclude_none=True)
    assert wire_payload["parent_tool_use_id"] == "toolu_subagent_01"


def test_legacy_persisted_conversation_without_parent_tool_use_id_loads(tmp_path):
    """Persisted event JSON files written pre-feature still load cleanly."""
    persistence_dir = tmp_path / "conversations"
    conv_id = uuid4()
    llm = LLM(model="gpt-4o", usage_id="test")
    agent = Agent(llm=llm, tools=[])

    conv = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        persistence_dir=str(persistence_dir),
        conversation_id=conv_id,
    )
    conv.send_message("hello from legacy")
    conv.close()

    # Strip parent_tool_use_id from persisted files to simulate pre-feature disk state
    events_dir = persistence_dir / conv_id.hex / "events"
    event_files = list(events_dir.glob("*.json"))
    assert event_files, "Expected persisted event files on disk"
    for ef in event_files:
        data = json.loads(ef.read_text(encoding="utf-8"))
        data.pop("parent_tool_use_id", None)
        ef.write_text(json.dumps(data), encoding="utf-8")

    resumed = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        persistence_dir=str(persistence_dir),
        conversation_id=conv_id,
    )
    try:
        loaded_events = list(resumed.state.events)
        assert len(loaded_events) >= 1
        assert all(ev.parent_tool_use_id is None for ev in loaded_events)
    finally:
        resumed.close()


def test_parent_tool_use_id_is_not_corrupted_by_secret_masking():
    """ToolCallID carries SkipSecretMasking so masking preserves correlation IDs."""
    secret = "toolu_overlap"
    registry = SecretRegistry()
    registry.update_secrets({"TOKEN": secret})
    registry.get_secret_value("TOKEN")

    event = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[TextContent(text=f"leaked {secret}")],
        ),
        parent_tool_use_id=f"{secret}_call_1",
    )
    masked = registry.mask_secrets_in_model(event)
    assert masked.parent_tool_use_id == f"{secret}_call_1"
    assert "<secret-hidden>" in masked.llm_message.content[0].text
