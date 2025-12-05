"""Tests for tool schema summary field enhancement.

This module tests that the summary field is properly added to tool schemas
when add_summary_prediction is enabled.
"""

from collections.abc import Sequence
from typing import ClassVar

from pydantic import Field

from openhands.sdk.tool import Action, Observation, ToolDefinition


class TSAction(Action):
    x: int = Field(description="x")


class MockSummaryTool1(ToolDefinition[TSAction, Observation]):
    """Concrete mock tool for summary testing."""

    name: ClassVar[str] = "t1"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSummaryTool1"]:
        return [cls(**params)]


class MockSummaryTool2(ToolDefinition[TSAction, Observation]):
    """Concrete mock tool for summary testing."""

    name: ClassVar[str] = "t2"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSummaryTool2"]:
        return [cls(**params)]


def test_to_responses_tool_summary_added():
    """Test that summary field is added when requested."""
    tool = MockSummaryTool1(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # add_summary_prediction=True -> add summary field
    t = tool.to_responses_tool(add_summary_prediction=True)
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props

    # Verify summary field has correct schema
    summary_field = props["summary"]
    assert summary_field["type"] == "string"
    assert "description" in summary_field


def test_to_responses_tool_summary_not_added():
    """Test that summary field is not added when not requested."""
    tool = MockSummaryTool2(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # add_summary_prediction=False -> do not add summary field
    t = tool.to_responses_tool(add_summary_prediction=False)
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" not in props


def test_to_responses_tool_summary_and_security():
    """Test that summary and security_risk can both be added."""
    tool = MockSummaryTool1(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # Both flags enabled -> both fields should be present
    t = tool.to_responses_tool(
        add_security_risk_prediction=True,
        add_summary_prediction=True,
    )
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props
    assert "security_risk" in props


def test_to_openai_tool_summary_added():
    """Test that summary field is added to OpenAI tool schema."""
    tool = MockSummaryTool1(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # add_summary_prediction=True -> add summary field
    t = tool.to_openai_tool(add_summary_prediction=True)
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


def test_to_openai_tool_summary_not_added():
    """Test that summary field is not added to OpenAI tool schema when not requested."""
    tool = MockSummaryTool2(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # add_summary_prediction=False -> do not add summary field
    t = tool.to_openai_tool(add_summary_prediction=False)
    func = t.get("function")
    assert func is not None
    params = func.get("parameters")
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" not in props


def test_to_openai_tool_summary_and_security():
    """Test that summary and security_risk can both be added to OpenAI schema."""
    tool = MockSummaryTool1(
        description="Test tool",
        action_type=TSAction,
        observation_type=None,
        annotations=None,
    )

    # Both flags enabled -> both fields should be present
    t = tool.to_openai_tool(
        add_security_risk_prediction=True,
        add_summary_prediction=True,
    )
    func = t.get("function")
    assert func is not None
    params = func.get("parameters")
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "summary" in props
    assert "security_risk" in props
