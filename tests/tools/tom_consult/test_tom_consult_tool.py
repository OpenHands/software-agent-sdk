"""Tests for TomConsultTool declared_resources."""

import pytest

from openhands.sdk.tool import DeclaredResources
from openhands.tools.tom_consult.definition import (
    ConsultTomAction,
    ConsultTomObservation,
    TomConsultTool,
)


@pytest.mark.parametrize(
    "action",
    [
        ConsultTomAction(reason="unclear intent", use_user_message=True),
        ConsultTomAction(
            reason="need guidance",
            use_user_message=False,
            custom_query="What does the user prefer?",
        ),
    ],
    ids=["use-user-message", "custom-query"],
)
def test_consult_tom_declared_resources(action):
    """TomConsultTool always declares safe with no resource keys."""
    tool = TomConsultTool(
        action_type=ConsultTomAction,
        observation_type=ConsultTomObservation,
        description="test",
        executor=None,
    )

    resources = tool.declared_resources(action)

    assert isinstance(resources, DeclaredResources)
    assert resources.declared is True
    assert resources.keys == ()
