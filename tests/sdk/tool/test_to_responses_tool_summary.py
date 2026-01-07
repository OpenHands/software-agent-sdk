"""Tests for tool schema summary field enhancement."""

from collections.abc import Sequence
from typing import ClassVar

import pytest
from pydantic import Field

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
