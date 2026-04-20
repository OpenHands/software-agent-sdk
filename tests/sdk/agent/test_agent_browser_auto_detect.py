from __future__ import annotations

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool


def _make_llm() -> LLM:
    return LLM(model="test-model", api_key=SecretStr("test-key"), usage_id="test-llm")


@pytest.mark.parametrize(
    "tools, prompt_kwargs, expect_browser",
    [
        pytest.param(
            [Tool(name="browser_tool_set")], {}, True, id="browser_tool_present"
        ),
        pytest.param([], {}, False, id="no_tools"),
        pytest.param(
            [Tool(name="terminal_tool"), Tool(name="file_editor_tool")],
            {},
            False,
            id="other_tools_only",
        ),
        pytest.param(
            [Tool(name="browser_tool_set")],
            {"enable_browser": False},
            False,
            id="explicit_override_false",
        ),
    ],
)
def test_browser_auto_detect(tools, prompt_kwargs, expect_browser):
    agent = Agent(llm=_make_llm(), tools=tools, system_prompt_kwargs=prompt_kwargs)
    msg = agent.static_system_message
    if expect_browser:
        assert "<BROWSER_TOOLS>" in msg
    else:
        assert "<BROWSER_TOOLS>" not in msg
