"""Shared fixtures for conversation content search tests."""

import json
import uuid
from pathlib import Path

import pytest

from openhands.sdk.conversation.persistence_const import (
    EVENT_FILE_PATTERN,
    EVENTS_DIR,
)


def write_conversation(
    root: Path,
    title: str,
    messages: list[tuple[str, str]] | None = None,
    events: list[dict] | None = None,
    *,
    conv_id: str | None = None,
) -> str:
    """Write a conversation directory in the persisted storage format.

    Mirrors ``FileConversationStore`` on disk: the directory is named by the
    undashed ``UUID.hex`` form, ``meta.json`` carries the dashed id and
    title, and event files follow ``EVENT_FILE_PATTERN``. Search hits'
    conversation ids are the dashed form, so tests can assert both formats
    round-trip.
    """
    dashed = conv_id or str(uuid.uuid4())
    conv_dir = root / dashed.replace("-", "")
    events_dir = conv_dir / EVENTS_DIR
    events_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "meta.json").write_text(
        json.dumps(
            {
                "conversation_id": dashed,
                "id": dashed,
                "title": title,
                "workspace": {"working_dir": str(conv_dir)},
            }
        )
    )
    event_dicts = events if events is not None else []
    for i, (source, text) in enumerate(messages or []):
        event_dicts.append(
            {
                "id": str(uuid.uuid4()),
                "timestamp": "2026-11-01T10:00:00",
                "source": source,
                "llm_message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
    for i, event in enumerate(event_dicts):
        event_id = event.get("id") or str(uuid.uuid4())
        (events_dir / EVENT_FILE_PATTERN.format(idx=i, event_id=event_id)).write_text(
            json.dumps(event)
        )
    return dashed


@pytest.fixture
def conv_root(tmp_path):
    convs = tmp_path / "conversations"
    convs.mkdir()
    write_conversation(
        convs,
        "Garage door sensor",
        [
            ("user", "The garage door sensor keeps triggering false alarms."),
            ("agent", "False alarms happen below five degrees at night."),
        ],
    )
    write_conversation(
        convs,
        "Italy trip",
        [("user", "Draft a ten day itinerary for Italy starting in Rome.")],
    )
    write_conversation(
        convs,
        "Secret keeper",
        [("user", "Store this key: sk-ABCDEFGHIJKLMNOPQRSTUV please.")],
    )
    write_conversation(
        convs,
        "ML platform",
        [("agent", "The Hugging Face Hub hosts transformers models.")],
    )
    write_conversation(
        convs,
        "Household chores",
        [("user", "I was doing laundry when the door bell rang.")],
    )
    write_conversation(
        convs,
        "Quokka photos",
        [],
        events=[
            {
                "id": str(uuid.uuid4()),
                "timestamp": "2026-11-01T10:00:00",
                "source": "agent",
                "llm_message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "The quokka sat calmly in its enclosure.",
                            "signature": None,
                        }
                    ],
                },
            }
        ],
    )
    write_conversation(
        convs,
        "Riverbank notes",
        [],
        events=[
            {
                "id": str(uuid.uuid4()),
                "timestamp": "2026-11-01T10:00:00",
                "source": "user",
                "llm_message": {
                    "role": "user",
                    "content": "The capybara napped by the riverbank.",
                },
            }
        ],
    )
    write_conversation(
        convs,
        "Skip me",
        [("user", "One valid message about llamas.")],
        events=[
            {
                "id": None,
                "timestamp": "2026-11-01T10:00:00",
                "source": "agent",
                "llm_message": {
                    "role": "assistant",
                    "content": "No id on this event so it cannot be indexed.",
                },
            }
        ],
    )
    return convs
