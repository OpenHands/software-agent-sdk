"""Persisted MCP tools must load under either mcp major (#5251)."""

import json
import uuid
from pathlib import Path
from unittest.mock import Mock

import mcp.types
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic.alias_generators import to_camel

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import Event, SystemPromptEvent
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition


# Verbatim Tool.model_dump(mode="json", exclude_none=True) under mcp 2.2.0,
# i.e. what SDK 1.49.0-1.49.1 wrote to events/. Note the schema property
# "mime_type", which is user data and must survive unrenamed.
MCP2_TOOL = {
    "name": "read_file",
    "title": "Read",
    "description": "Read a file",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mime_type": {"type": "string"},
        },
        "required": ["path"],
    },
    "execution": {"task_support": "optional"},
    "output_schema": {"type": "object"},
    "icons": [{"src": "https://x/i.png", "mime_type": "image/png", "sizes": ["16x16"]}],
    "annotations": {"read_only_hint": True, "open_world_hint": False},
    "meta": {"k": 1},
}

MCP1_TOOL = {
    "name": "read_file",
    "title": "Read",
    "description": "Read a file",
    "inputSchema": MCP2_TOOL["input_schema"],
    "execution": {"taskSupport": "optional"},
    "outputSchema": {"type": "object"},
    "icons": [{"src": "https://x/i.png", "mimeType": "image/png", "sizes": ["16x16"]}],
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
    "_meta": {"k": 1},
}

# SDK 1.49.4 (mcp 1.x) dumped by field name: camelCase, but "meta" not "_meta".
MCP1_BY_NAME_TOOL = {("meta" if k == "_meta" else k): v for k, v in MCP1_TOOL.items()}


class _Mcp2StyleAnnotations(BaseModel):
    """Shape of mcp 2.x ToolAnnotations: snake_case attributes, camelCase aliases."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    read_only_hint: bool | None = None
    open_world_hint: bool | None = None


def _persisted_system_prompt(raw_tool: dict) -> str:
    tool = MCPToolDefinition.create(
        mcp.types.Tool.model_validate(MCP1_TOOL), Mock(spec=MCPClient)
    )[0]
    event = SystemPromptEvent(system_prompt=TextContent(text="sys"), tools=[tool])
    payload = json.loads(event.model_dump_json(exclude_none=True))
    payload["tools"][0]["mcp_tool"] = raw_tool
    return json.dumps(payload)


@pytest.mark.parametrize(
    "raw_tool",
    [MCP2_TOOL, MCP1_BY_NAME_TOOL, MCP1_TOOL],
    ids=["sdk-1.49.1-mcp2", "sdk-1.49.4-mcp1", "wire"],
)
def test_persisted_mcp_tool_loads_in_either_spelling(raw_tool):
    event = Event.model_validate_json(_persisted_system_prompt(raw_tool))

    assert isinstance(event, SystemPromptEvent)
    tool = event.tools[0]
    assert isinstance(tool, MCPToolDefinition)
    wire = tool.mcp_tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert wire["inputSchema"]["properties"].keys() == {"path", "mime_type"}
    assert wire["outputSchema"] == {"type": "object"}
    assert wire["annotations"] == {"readOnlyHint": True, "openWorldHint": False}
    assert wire["icons"][0]["mimeType"] == "image/png"
    assert wire["execution"] == {"taskSupport": "optional"}
    assert wire["_meta"] == {"k": 1}


def test_mcp_tool_is_written_in_wire_spelling():
    event = Event.model_validate_json(_persisted_system_prompt(MCP2_TOOL))
    written = json.loads(event.model_dump_json(exclude_none=True))["tools"][0][
        "mcp_tool"
    ]

    assert written == MCP1_TOOL
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_minimal_mcp_tool_with_nulls_loads():
    raw = {
        "name": "t",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "annotations": None,
        "icons": None,
        "execution": None,
        "meta": None,
    }
    event = Event.model_validate_json(_persisted_system_prompt(raw))

    assert isinstance(event, SystemPromptEvent)
    tool = event.tools[0]
    assert isinstance(tool, MCPToolDefinition)
    wire = tool.mcp_tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert wire == {"name": "t", "inputSchema": {"type": "object"}}


@pytest.mark.parametrize(
    "annotations",
    [
        mcp.types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        _Mcp2StyleAnnotations(read_only_hint=True, open_world_hint=False),
    ],
    ids=["installed-mcp", "mcp2-shape"],
)
def test_create_keeps_mcp_annotations(annotations):
    mcp_tool = mcp.types.Tool.model_validate(MCP1_TOOL).model_copy(
        update={"annotations": annotations}
    )
    tool = MCPToolDefinition.create(mcp_tool, Mock(spec=MCPClient))[0]

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is False


def _conversation_with_mcp_tool(tmp_path) -> tuple[uuid.UUID, Path]:
    tool = MCPToolDefinition.create(
        mcp.types.Tool.model_validate(MCP1_TOOL), Mock(spec=MCPClient)
    )[0]
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="test-llm")
    conv = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(tmp_path / "convs"),
        visualizer=None,
    )
    event = SystemPromptEvent(system_prompt=TextContent(text="sys"), tools=[tool])
    conv.state.events.append(event)
    conv_id = conv.id
    conv.close()
    event_file = next((tmp_path / "convs").rglob(f"event-*-{event.id}.json"))
    return conv_id, event_file


def _resume(tmp_path, conv_id) -> LocalConversation:
    return LocalConversation(
        agent=None,
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(tmp_path / "convs"),
        conversation_id=conv_id,
        visualizer=None,
    )


def _set_persisted_mcp_tool(event_file: Path, raw_tool: dict) -> None:
    payload = json.loads(event_file.read_text())
    payload["tools"][0]["mcp_tool"] = raw_tool
    event_file.write_text(json.dumps(payload))


def test_resume_conversation_persisted_under_mcp2(tmp_path):
    conv_id, event_file = _conversation_with_mcp_tool(tmp_path)
    _set_persisted_mcp_tool(event_file, MCP2_TOOL)

    conv = _resume(tmp_path, conv_id)

    prompts = [e for e in conv.state.events if isinstance(e, SystemPromptEvent)]
    assert prompts[-1].tools[0].name == "read_file"
    conv.close()
