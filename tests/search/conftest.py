"""Shared fixtures for conversation content search tests."""

import json
import uuid
from pathlib import Path

import pytest


def write_conversation(root: Path, title: str, messages: list) -> str:
    """Write a conversation directory with meta.json and message events."""
    conv_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, title))
    conv_dir = root / conv_id
    events_dir = conv_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "meta.json").write_text(
        json.dumps(
            {
                "conversation_id": conv_id,
                "id": conv_id,
                "title": title,
                "workspace": {"working_dir": str(conv_dir)},
            }
        )
    )
    for i, (source, text) in enumerate(messages):
        event = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{title}:{i}")),
            "timestamp": "2026-11-01T10:00:00",
            "source": source,
            "llm_message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        (events_dir / f"event-{i:06d}-{event['id']}.json").write_text(json.dumps(event))
    return conv_id


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
    return convs
