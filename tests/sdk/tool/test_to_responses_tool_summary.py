"""Tests for tool schema summary field enhancement."""

from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import Mock

import mcp.types
import pytest
from pydantic import Field

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool import Action, Observation, ToolDefinition


class TSAction(Action):
    x: int = Field(description="x")


class MockSummaryTool(ToolDefinition[TSAction, Observation]):
    """Concrete mock tool for summary testing."""

    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSummaryTool"]:
        return [cls(**params)]


@pytest.fixture
def tool():
    return MockSummaryTool(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )


def test_to_responses_tool_summary_always_added(tool):
    """Test that summary field is always added to responses tool schema."""
    t = tool.to_responses_tool()
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert "summary" in props
    assert props["summary"]["type"] == "string"


def test_to_openai_tool_summary_always_added(tool):
    """Test that summary field is always added to OpenAI tool schema."""
    t = tool.to_openai_tool()
    func = t.get("function")
    assert func is not None
    params = func.get("parameters")
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert "summary" in props
    assert props["summary"]["type"] == "string"


def test_mcp_tool_with_summary_param_preserves_original_description():
    """Schema injection must not shadow a tool's own 'summary' field."""
    mcp_tool = mcp.types.Tool(
        name="jira_create_issue",
        description="Create a Jira issue",
        inputSchema={
            "type": "object",
            "properties": {
                "project_key": {"type": "string"},
                "summary": {
                    "type": "string",
                    "description": "Ticket title",
                },
                "issue_type": {"type": "string"},
            },
            "required": ["project_key", "summary", "issue_type"],
        },
    )
    client = Mock(spec=MCPClient)
    tool = MCPToolDefinition.create(mcp_tool, client)[0]

    openai_tool = tool.to_openai_tool()
    func = openai_tool.get("function")
    assert func is not None
    params = func.get("parameters")
    assert isinstance(params, dict)
    props = params.get("properties") or {}

    # The tool's own "summary" field should be present with its
    # original description, NOT the SDK's meta-summary description.
    assert "summary" in props
    assert props["summary"]["description"] == "Ticket title"
