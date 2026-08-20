from pydantic import BaseModel

from openhands.sdk import LLM, Conversation
from openhands.sdk.tool.builtins import FinishTool
from openhands.sdk.tool.registry import (
    list_registered_tools,
    register_tool,
    unregister_tool,
)
from openhands.tools.preset.default import get_default_agent


class _FinishResult(BaseModel):
    success: bool
    outcome_summary: str


def test_default_agent_registers_finish_tool_for_structured_response(caplog):
    was_registered = FinishTool.__name__ in list_registered_tools()
    unregister_tool(FinishTool.__name__)

    try:
        llm = LLM(model="test-model", usage_id="test-llm")
        agent = get_default_agent(
            llm=llm,
            cli_mode=True,
            finish_tool_response_schema=_FinishResult,
        )

        assert any(
            tool.name == "FinishTool"
            and tool.params == {"response_schema": _FinishResult}
            for tool in agent.tools
        )
        assert "FinishTool" in list_registered_tools()
        assert "FinishTool" not in agent.include_default_tools
        assert "ThinkTool" in agent.include_default_tools

        caplog.clear()
        get_default_agent(
            llm=llm,
            cli_mode=True,
            finish_tool_response_schema=_FinishResult,
        )
        assert "Duplicate tool name" not in caplog.text

        conv = Conversation(agent=agent, visualizer=None)
        conv._ensure_agent_ready()

        finish_tool = agent.tools_map["finish"]
        assert finish_tool.response_schema is _FinishResult
        assert "think" in agent.tools_map
    finally:
        unregister_tool(FinishTool.__name__)
        if was_registered:
            register_tool(FinishTool.__name__, FinishTool)
