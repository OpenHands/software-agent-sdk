from collections.abc import Sequence
from typing import Any, ClassVar

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
    # readOnlyHint=True -> do not add security_risk even if requested
    readonly = MockSecurityTool1(
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
    writable = MockSecurityTool2(
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
    noflag = MockSecurityTool3(
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


def _extract_openai_properties(schema: object) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    fn = schema.get("function", {})
    if not isinstance(fn, dict):
        return {}
    params = fn.get("parameters", {})
    if not isinstance(params, dict):
        return {}
    props = params.get("properties", {})
    return props if isinstance(props, dict) else {}


def _extract_responses_properties(schema: object) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    params = schema.get("parameters", {})
    if not isinstance(params, dict):
        return {}
    props = params.get("properties", {})
    return props if isinstance(props, dict) else {}


def test_tool_custom_risk_description():
    from openhands.sdk.security.risk import (
        CLI_TIERS,
        SANDBOX_TIERS,
        get_security_risk_description,
    )

    custom_cli_desc = get_security_risk_description(cli_mode=True)
    custom_sandbox_desc = get_security_risk_description(cli_mode=False)

    writable = MockSecurityTool2(
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=False),
    )

    # 1. Custom risk description on Responses tool
    resp_tool = writable.to_responses_tool(
        add_security_risk_prediction=True,
        risk_description=custom_cli_desc,
    )
    props = _extract_responses_properties(resp_tool)
    assert props["security_risk"]["description"] == custom_cli_desc
    assert CLI_TIERS in props["security_risk"]["description"]

    # 2. Custom risk description on OpenAI tool
    openai_tool = writable.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=custom_sandbox_desc,
    )
    props2 = _extract_openai_properties(openai_tool)
    assert props2["security_risk"]["description"] == custom_sandbox_desc
    assert SANDBOX_TIERS in props2["security_risk"]["description"]

    # 3. Default description when None is passed
    default_tool = writable.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=None,
    )
    props3 = _extract_openai_properties(default_tool)
    assert CLI_TIERS not in props3["security_risk"]["description"]
    assert SANDBOX_TIERS not in props3["security_risk"]["description"]

    # 4. Read-only tool omits security_risk even when description is provided
    readonly = MockSecurityTool1(
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    ro_resp = readonly.to_responses_tool(
        add_security_risk_prediction=True,
        risk_description=custom_cli_desc,
    )
    assert "security_risk" not in _extract_responses_properties(ro_resp)

    ro_openai = readonly.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=custom_cli_desc,
    )
    assert "security_risk" not in _extract_openai_properties(ro_openai)


def test_varying_risk_descriptions_do_not_duplicate_action_subclasses():
    from openhands.sdk.security.risk import get_security_risk_description
    from openhands.sdk.utils.models import _get_checked_concrete_subclasses

    writable = MockSecurityTool2(
        description="d",
        action_type=TRTSAction,
        observation_type=None,
        annotations=ToolAnnotations(readOnlyHint=False),
    )

    writable.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=get_security_risk_description(cli_mode=True),
    )
    writable.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=get_security_risk_description(cli_mode=False),
    )
    writable.to_openai_tool(
        add_security_risk_prediction=True,
        risk_description=None,
    )

    checked = _get_checked_concrete_subclasses(Action)
    assert f"{TRTSAction.__name__}WithRisk" in checked
