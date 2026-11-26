import pytest
from acp.schema import PermissionOption, ToolCallUpdate


@pytest.fixture
def permission_request():
    return (
        "session",
        ToolCallUpdate(tool_call_id="fixture"),
        [PermissionOption(option_id="once", name="Allow once", kind="allow_once")],
    )
