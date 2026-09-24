import pytest

from openhands.sdk.tool.client_tool import (
    ClientToolRegistrationError,
    ClientToolSpec,
    register_client_tools,
)
from openhands.tools.terminal import TerminalTool


def test_register_client_tools_rejects_builtin_tool_name_collision() -> None:
    spec = ClientToolSpec(
        name=TerminalTool.name,
        description="Client terminal",
    )

    with pytest.raises(
        ClientToolRegistrationError,
        match="collides with an existing non-client tool",
    ):
        register_client_tools([spec])
