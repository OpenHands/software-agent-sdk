"""A conversation whose custom tool module is gone still resumes (#4080).

After a restart without the module that defines a custom tool, its stored
events name classes that are no longer loaded. The conversation must still
load, leave those events out of the branch, and run without the tool.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ValidationError

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import MessageEvent, SystemPromptEvent
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    registry,
)


TOOL_NAME = "resume_gone_echo"
# Stored event file names only accept hex-like ids.
A, B, C = "aaaaaaaa-0001", "bbbbbbbb-0002", "cccccccc-0003"


class _GoneAction(Action):
    text: str


class _GoneObs(Observation):
    out: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.out)]


class _GoneExec(ToolExecutor[_GoneAction, _GoneObs]):
    def __call__(self, action: _GoneAction, conversation=None) -> _GoneObs:
        return _GoneObs(out=action.text)


class _GoneTool(ToolDefinition[_GoneAction, _GoneObs]):
    name: ClassVar[str] = TOOL_NAME

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_GoneTool"]:
        return [
            cls(
                description="Echo",
                action_type=_GoneAction,
                observation_type=_GoneObs,
                executor=_GoneExec(),
            )
        ]


def _message(event_id: str, parent_id: str | None = None) -> MessageEvent:
    return MessageEvent(
        id=event_id,
        parent_id=parent_id,
        llm_message=Message(role="user", content=[TextContent(text=event_id)]),
        source="user",
    )


def _rename_kind(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert f'"{old}"' in text
    path.write_text(text.replace(f'"{old}"', f'"{new}"'))


@pytest.mark.parametrize("explicit_parents", [True, False], ids=["tree", "legacy"])
def test_path_to_root_skips_event_of_unknown_kind(explicit_parents):
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(_message(A))
    log.append(_message(B, parent_id=A if explicit_parents else None))
    log.append(_message(C, parent_id=B if explicit_parents else None))
    path = log._path(1)
    fs.write(path, fs.read(path).replace('"MessageEvent"', '"GoneEvent"'))

    fresh = EventLog(fs)
    with pytest.raises(ValidationError):
        fresh[1]
    assert [e.id for e in fresh.path_to_root(C)] == [A, C]


def test_path_to_root_still_raises_on_corrupt_json():
    fs = InMemoryFileStore()
    log = EventLog(fs)
    log.append(_message(A))
    log.append(_message(B, parent_id=A))
    fs.write(log._path(0), "{not json")

    with pytest.raises(ValidationError):
        EventLog(fs).path_to_root(B)


def test_resume_without_custom_tool_module(tmp_path, monkeypatch):
    registry.register_tool(TOOL_NAME, _GoneTool)
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"), tools=[Tool(name=TOOL_NAME)]
    )
    conv = LocalConversation(
        agent=agent,
        workspace=tmp_path / "ws",
        persistence_dir=tmp_path / "conv",
        visualizer=None,
    )
    conv._ensure_agent_ready()
    assert TOOL_NAME in conv.agent.tools_map
    leaf = conv.state.events[-1].id
    conv.state.append_event(_message(B, parent_id=leaf))
    conv.state.append_event(_message(C, parent_id=B))
    cid = conv.id
    conv.close()

    # Restart without the module: its tool is unregistered and every stored
    # class it defined is an unknown kind.
    monkeypatch.delitem(registry._REG, TOOL_NAME)
    events_dir = next((tmp_path / "conv").rglob("events"))
    _rename_kind(next(events_dir.glob(f"*{B}.json")), "MessageEvent", "Gone")
    system_file = next(events_dir.glob("event-00000-*.json"))
    _rename_kind(system_file, "_GoneTool", "GoneTool")

    resumed = LocalConversation(
        agent=None,
        workspace=tmp_path / "ws",
        persistence_dir=tmp_path / "conv",
        conversation_id=cid,
        visualizer=None,
    )
    branch = [e.id for e in resumed.state.active_branch()]
    assert B not in branch
    assert branch[-1] == C
    system = resumed.state.events[0]
    assert isinstance(system, SystemPromptEvent)
    assert TOOL_NAME not in {t.name for t in system.tools}

    resumed.send_message("still there?")
    assert TOOL_NAME not in resumed.agent.tools_map
    last = resumed.state.events[-1]
    assert isinstance(last, MessageEvent)
    assert json.loads(last.model_dump_json())["parent_id"] == C
    resumed.close()


def test_fresh_conversation_still_rejects_unregistered_tool(tmp_path):
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[Tool(name="never_registered_tool")],
    )
    conv = LocalConversation(agent=agent, workspace=tmp_path, visualizer=None)
    with pytest.raises(KeyError, match="never_registered_tool"):
        conv._ensure_agent_ready()
