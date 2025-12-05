"""Tests for tool schema summary field enhancement.

This module tests that the summary field is always properly added to tool
schemas for transparency and explainability.
"""

from collections.abc import Sequence
from typing import ClassVar

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


def test_to_responses_tool_summary_always_added():
    """Test that summary field is always added."""
    tool = MockSummaryTool(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # Summary field is always added
    t = tool.to_responses_tool()
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props

    # Verify summary field has correct schema
    summary_field = props["summary"]
    assert summary_field["type"] == "string"
    assert "description" in summary_field


def test_to_responses_tool_summary_and_security():
    """Test that summary and security_risk are both present."""
    tool = MockSummaryTool(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # Security risk enabled -> both fields should be present
    t = tool.to_responses_tool(add_security_risk_prediction=True)
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props
    assert "security_risk" in props


def test_to_openai_tool_summary_always_added():
    """Test that summary field is always added to OpenAI tool schema."""
    tool = MockSummaryTool(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # Summary field is always added
    t = tool.to_openai_tool()
    func = t.get("function")
    assert func is not None
    params = func.get("parameters")
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props

    # Verify summary field has correct schema
    summary_field = props["summary"]
    assert summary_field["type"] == "string"
    assert "description" in summary_field


def test_to_openai_tool_summary_and_security():
    """Test that summary and security_risk are both present in OpenAI schema."""
    tool = MockSummaryTool(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # Security risk enabled -> both fields should be present
    t = tool.to_openai_tool(add_security_risk_prediction=True)
    func = t.get("function")
    assert func is not None
    params = func.get("parameters")
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props
    assert "security_risk" in props
