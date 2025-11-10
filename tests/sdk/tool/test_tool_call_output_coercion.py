from collections.abc import Sequence

import pytest
from pydantic import Field

from openhands.sdk.tool import Observation, ToolDefinition, ToolExecutor
from openhands.sdk.tool.schema import Action


class OCAAction(Action):
    y: int = Field(description="y")


class OCAObs(Observation):
    value: int

    @property
    def to_llm_content(self):  # type: ignore[override]
        from openhands.sdk.llm import TextContent

        return [TextContent(text=str(self.value))]


class MockCoercionTool(ToolDefinition[OCAAction, OCAObs]):
    """Concrete mock tool for output coercion testing."""

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["MockCoercionTool"]:
        return [cls(**params)]


def test_tool_call_with_observation_none_result_shapes():
    # When observation_type is None, results are wrapped/coerced to Observation
    # 1) dict -> Observation
    class E1(ToolExecutor[OCAAction, dict[str, object]]):
        def __call__(self, action: OCAAction, conversation=None) -> dict[str, object]:
            return {"kind": "OCAObs", "value": 1}

    t = MockCoercionTool(
        description="d",
        action_type=OCAAction,
        observation_type=None,
        executor=E1(),
    )
    obs = t(OCAAction(y=1))
    assert isinstance(obs, Observation)

    # 2) Observation subclass -> Observation passthrough
    class MObs(Observation):
        value: int

        @property
        def to_llm_content(self):  # type: ignore[override]
            from openhands.sdk.llm import TextContent

            return [TextContent(text=str(self.value))]

    class E2(ToolExecutor[OCAAction, MObs]):
        def __call__(self, action: OCAAction, conversation=None) -> MObs:
            return MObs(value=2)

    t2 = MockCoercionTool(
        description="d",
        action_type=OCAAction,
        observation_type=None,
        executor=E2(),
    )
    obs2 = t2(OCAAction(y=2))
    assert isinstance(obs2, Observation)
    assert isinstance(obs2, MObs)

    # 3) invalid type -> raises TypeError
    class E3(ToolExecutor[OCAAction, list[int]]):
        def __call__(self, action: OCAAction, conversation=None) -> list[int]:
            return [1, 2, 3]

    t3 = MockCoercionTool(
        description="d",
        action_type=OCAAction,
        observation_type=None,
        executor=E3(),
    )
    with pytest.raises(TypeError, match="Output must be dict or BaseModel"):
        t3(OCAAction(y=3))
