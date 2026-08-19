from pydantic import BaseModel

from openhands.sdk import LLM, Conversation
from openhands.sdk.tool import registry as tool_registry
from openhands.tools.preset.default import get_default_agent


class _FinishResult(BaseModel):
    success: bool
    outcome_summary: str


def test_default_agent_can_parameterize_finish_tool_without_prior_registration():
    with tool_registry._LOCK:
        saved_resolver = tool_registry._REG.pop("FinishTool", None)
        saved_checker = tool_registry._USABILITY_REG.pop("FinishTool", None)
        saved_module = tool_registry._MODULE_QUALNAMES.pop("FinishTool", None)

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
        assert "FinishTool" in agent.include_default_tools
        assert "ThinkTool" in agent.include_default_tools

        conv = Conversation(agent=agent, visualizer=None)
        conv._ensure_agent_ready()

        finish_tool = agent.tools_map["finish"]
        assert finish_tool.response_schema is _FinishResult
        assert "think" in agent.tools_map
    finally:
        with tool_registry._LOCK:
            tool_registry._REG.pop("FinishTool", None)
            tool_registry._USABILITY_REG.pop("FinishTool", None)
            tool_registry._MODULE_QUALNAMES.pop("FinishTool", None)
            if saved_resolver is not None:
                tool_registry._REG["FinishTool"] = saved_resolver
            if saved_checker is not None:
                tool_registry._USABILITY_REG["FinishTool"] = saved_checker
            if saved_module is not None:
                tool_registry._MODULE_QUALNAMES["FinishTool"] = saved_module
