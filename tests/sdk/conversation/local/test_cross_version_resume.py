"""Conversations persisted by earlier releases must keep resuming (#5252).

Fixtures are generated with
``.github/scripts/check_conversation_compat.py --sdk-version X --save-to DIR``.
"""

import json
import shutil
import uuid
from pathlib import Path

import pytest

import openhands.tools  # noqa: F401  # registers tool kinds, as the server does
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ObservationEvent, SystemPromptEvent
from openhands.sdk.mcp.definition import MCPToolObservation
from openhands.sdk.mcp.tool import MCPToolDefinition


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "conversations"
CASES = sorted(p for p in FIXTURES.iterdir() if (p / "events").is_dir())
REQUIRED_EVENT_KINDS = {
    "SystemPromptEvent",
    "MessageEvent",
    "ActionEvent",
    "ObservationEvent",
    "AgentErrorEvent",
}


def test_fixtures_are_discovered():
    assert {"v1_49_1_mcp", "v1_49_4_mcp"} <= {p.name for p in CASES}


@pytest.mark.parametrize("fixture", CASES, ids=lambda p: p.name)
def test_persisted_conversation_resumes(fixture: Path, tmp_path: Path):
    state = json.loads((fixture / "base_state.json").read_text())
    conversation_id = uuid.UUID(state["id"])
    shutil.copytree(fixture, tmp_path / "conversations" / conversation_id.hex)

    conversation = LocalConversation(
        agent=None,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
        conversation_id=conversation_id,
        visualizer=None,
    )
    try:
        events = list(conversation.state.events)
    finally:
        conversation.close()

    assert len(events) == len(list((fixture / "events").glob("event-*.json")))
    assert REQUIRED_EVENT_KINDS <= {type(event).__name__ for event in events}
    if fixture.name.endswith("_mcp"):
        assert any(
            isinstance(event, ObservationEvent)
            and isinstance(event.observation, MCPToolObservation)
            for event in events
        )
        tools = [
            tool
            for event in events
            if isinstance(event, SystemPromptEvent)
            for tool in event.tools
            if isinstance(tool, MCPToolDefinition)
        ]
        assert [tool.name for tool in tools] == ["read_file"]
        wire = tools[0].mcp_tool.model_dump(mode="json", by_alias=True)
        assert set(wire["inputSchema"]["properties"]) == {"path", "mime_type"}
