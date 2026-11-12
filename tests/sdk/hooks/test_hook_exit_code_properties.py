"""Property test for the exit-code contract of command hooks.

Whatever JSON a hook prints on stdout, exit code 0 means success, exit code 2
blocks the operation, and a hook that ran to completion is never reported as
an internal executor error.
"""

import json

import pytest
from hypothesis import given, settings, strategies as st

from openhands.sdk.hooks.config import HookDefinition
from openhands.sdk.hooks.executor import HookExecutor
from openhands.sdk.hooks.types import HookEvent, HookEventType
from tests.command_utils import python_command


EVENT = HookEvent(
    event_type=HookEventType.PRE_TOOL_USE,
    tool_name="BashTool",
    tool_input={"command": "ls -la"},
    session_id="test-session",
)

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=6), children, max_size=3)
    ),
    max_leaves=6,
)
hook_outputs = (
    st.fixed_dictionaries(
        {},
        optional={
            "decision": st.sampled_from(["allow", "deny", "Deny"]) | json_values,
            "reason": json_values,
            "additionalContext": json_values,
            "continue": json_values,
        },
    )
    | json_values
)
exit_codes = st.sampled_from([0, 1, 2]) | st.integers(min_value=3, max_value=255)


@pytest.fixture(scope="module")
def executor(tmp_path_factory: pytest.TempPathFactory) -> HookExecutor:
    return HookExecutor(working_dir=str(tmp_path_factory.mktemp("hooks")))


@settings(max_examples=50)
@given(stdout=hook_outputs, exit_code=exit_codes)
def test_exit_code_contract_holds_whatever_the_hook_prints(
    executor: HookExecutor, stdout: object, exit_code: int
):
    script = (
        f"import sys; sys.stdout.write({json.dumps(stdout)!r}); sys.exit({exit_code})"
    )

    result = executor.execute(HookDefinition(command=python_command(script)), EVENT)

    assert result.error is None
    assert result.exit_code == exit_code
    assert result.success == (exit_code == 0)
    if exit_code == 2:
        assert result.blocked
        assert not result.should_continue
