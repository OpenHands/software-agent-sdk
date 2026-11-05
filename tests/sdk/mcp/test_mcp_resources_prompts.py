"""Tests for MCP Resources and Prompts access (#6191)."""

import base64
import sys
from unittest.mock import patch

import mcp.types
import pytest
from mcp.shared.exceptions import McpError

from openhands.sdk.mcp import (
    MCPCapabilityNotSupportedError,
    MCPClient,
    MCPServer,
    list_mcp_prompts,
    list_mcp_resources,
    read_mcp_resource,
    render_mcp_prompt,
)


def _server(module: str) -> MCPServer:
    return MCPServer(command=sys.executable, args=["-m", module])


@pytest.fixture
def config() -> dict[str, MCPServer]:
    return {"kanban": _server("tests.sdk.mcp.resources_prompts_server")}


def test_list_resources_returns_resources_and_templates(config):
    listing = list_mcp_resources(config)["kanban"]

    by_uri = {r.uri: r for r in listing.resources}
    assert "docs://usage" in by_uri
    usage = by_uri["docs://usage"]
    assert usage.server == "kanban"
    assert usage.name == "usage-guide"
    assert usage.mime_type == "text/plain"
    assert usage.description is not None

    assert len(listing.templates) == 1
    template = listing.templates[0]
    assert template.server == "kanban"
    assert template.uri_template == "docs://projects/{project_id}"
    assert template.parameters == ["project_id"]


def test_read_resource_and_resolved_template(config):
    usage = read_mcp_resource(config, "kanban", "docs://usage")
    assert usage[0].server == "kanban"
    assert usage[0].uri == "docs://usage"
    assert usage[0].mime_type == "text/plain"
    usage_text = usage[0].text
    assert usage_text is not None
    assert "Create a card" in usage_text

    resolved = read_mcp_resource(config, "kanban", "docs://projects/alpha")
    resolved_text = resolved[0].text
    assert resolved_text is not None
    assert resolved_text == "Notes for project alpha."


def test_read_binary_resource_returns_base64_blob(config):
    logo = read_mcp_resource(config, "kanban", "images://logo")
    assert logo[0].mime_type == "image/png"
    blob = logo[0].blob_b64
    assert blob is not None
    assert base64.b64decode(blob) == b"\x89PNG\r\n\x1a\nlogo-bytes"


def test_list_and_render_prompt_preserves_messages(config):
    prompts = list_mcp_prompts(config)
    assert len(prompts) == 1
    prompt = prompts[0]
    assert prompt.server == "kanban"
    assert prompt.name == "plan_task"
    arg_names = {a.name: a for a in prompt.arguments}
    assert arg_names["task"].required is True
    assert arg_names["project"].required is False

    rendered = render_mcp_prompt(
        config, "kanban", "plan_task", {"task": "ship the release", "project": "core"}
    )
    assert rendered.server == "kanban"
    assert rendered.name == "plan_task"
    assert len(rendered.messages) == 1
    message = rendered.messages[0]
    assert message.role == "user"
    assert message.content.type == "text"
    text = message.content.text
    assert text is not None
    assert "ship the release" in text
    assert "core" in text


def test_multi_server_results_identify_their_server():
    config = {
        "kanban": _server("tests.sdk.mcp.resources_prompts_server"),
        "tools_only": _server("tests.sdk.mcp.stdio_test_server"),
    }

    listings = list_mcp_resources(config)
    assert set(listings) == {"kanban", "tools_only"}
    assert listings["tools_only"].resources == []
    assert listings["tools_only"].templates == []
    for resource in listings["kanban"].resources + listings["kanban"].templates:
        assert resource.server == "kanban"

    prompts = list_mcp_prompts(config)
    assert [p.server for p in prompts] == ["kanban"]
    assert [p.name for p in prompts] == ["plan_task"]


def test_unknown_server_key_raises_value_error(config):
    with pytest.raises(ValueError, match="not configured"):
        read_mcp_resource(config, "nope", "docs://usage")


def test_failed_read_names_server_and_uri(config):
    with pytest.raises(Exception, match="kanban") as exc_info:
        read_mcp_resource(config, "kanban", "docs://missing")
    assert "docs://missing" in str(exc_info.value)


def test_method_not_found_maps_to_capability_error(config):
    error = McpError(
        error=mcp.types.ErrorData(
            code=mcp.types.METHOD_NOT_FOUND, message="Method not found"
        )
    )
    with patch.object(MCPClient, "list_resources", side_effect=error):
        with pytest.raises(MCPCapabilityNotSupportedError) as exc_info:
            list_mcp_resources(config, "kanban")
    assert exc_info.value.server == "kanban"
    assert exc_info.value.capability == "resources"
    assert "does not support" in str(exc_info.value)
