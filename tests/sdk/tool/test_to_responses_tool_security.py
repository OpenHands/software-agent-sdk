from collections.abc import Sequence
from typing import ClassVar

from pydantic import Field

from openhands.sdk.tool import Action, Observation, ToolAnnotations, ToolDefinition


class TRTSAction(Action):
    x: int = Field(description="x")


class MockSecurityTool1(ToolDefinition[TRTSAction, Observation]):
    """Concrete mock tool for security testing - readonly."""

    name: ClassVar[str] = "t1"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSecurityTool1"]:
        return [cls(**params)]


class MockSecurityTool2(ToolDefinition[TRTSAction, Observation]):
    """Concrete mock tool for security testing - writable."""

    name: ClassVar[str] = "t2"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSecurityTool2"]:
        return [cls(**params)]


class MockSecurityTool3(ToolDefinition[TRTSAction, Observation]):
    """Concrete mock tool for security testing - no flag."""

    name: ClassVar[str] = "t3"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSecurityTool3"]:
        return [cls(**params)]


def test_to_responses_tool_security_gating():
    # security_risk field is now always included regardless of readOnlyHint
    readonly = MockSecurityTool1(
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    t = readonly.to_responses_tool()
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "security_risk" in props  # Always included now

    # readOnlyHint=False -> also includes security_risk
    writable = MockSecurityTool2(
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=False),
    )
    t2 = writable.to_responses_tool()
    params2 = t2["parameters"]
    assert isinstance(params2, dict)
    props2 = params2.get("properties") or {}
    assert isinstance(props2, dict)
    assert "security_risk" in props2

    # add_security_risk_prediction=False -> never add
    noflag = MockSecurityTool3(
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=None,
    )
    t3 = noflag.to_responses_tool()
    params3 = t3["parameters"]
    assert isinstance(params3, dict)
    props3 = params3.get("properties") or {}
    assert isinstance(props3, dict)
    assert "security_risk" not in props3
