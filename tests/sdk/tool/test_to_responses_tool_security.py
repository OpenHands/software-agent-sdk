from collections.abc import Sequence

from pydantic import Field

from openhands.sdk.tool import Action, Observation, ToolAnnotations, ToolDefinition


class TRTSAction(Action):
    x: int = Field(description="x")


class MockSecurityTool(ToolDefinition[TRTSAction, Observation]):
    """Concrete mock tool for security testing."""

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockSecurityTool"]:
        return [cls(**params)]


def test_to_responses_tool_security_gating():
    # readOnlyHint=True -> do not add security_risk even if requested
    readonly = MockSecurityTool(
        name="t1",
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    t = readonly.to_responses_tool(add_security_risk_prediction=True)
    params = t["parameters"]
    assert isinstance(params, dict)
    props = params.get("properties") or {}
    assert isinstance(props, dict)
    assert "security_risk" not in props

    # readOnlyHint=False -> add when requested
    writable = MockSecurityTool(
        name="t2",
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=False),
    )
    t2 = writable.to_responses_tool(add_security_risk_prediction=True)
    params2 = t2["parameters"]
    assert isinstance(params2, dict)
    props2 = params2.get("properties") or {}
    assert isinstance(props2, dict)
    assert "security_risk" in props2

    # add_security_risk_prediction=False -> never add
    noflag = MockSecurityTool(
        name="t3",
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=None,
    )
    t3 = noflag.to_responses_tool(add_security_risk_prediction=False)
    params3 = t3["parameters"]
    assert isinstance(params3, dict)
    props3 = params3.get("properties") or {}
    assert isinstance(props3, dict)
    assert "security_risk" not in props3
