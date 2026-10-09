from pydantic import BaseModel

from openhands.sdk import LLM, Conversation
from openhands.sdk.tool.builtins import FinishTool
from openhands.sdk.tool.registry import list_registered_tools, register_tool
from openhands.tools.preset import default as default_preset
from openhands.tools.preset.default import get_default_agent


class _FinishResult(BaseModel):
    success: bool
    outcome_summary: str


def test_default_agent_registers_finish_tool_when_missing(monkeypatch):
    registered_tools: list[tuple[str, type[FinishTool]]] = []

    monkeypatch.setattr(default_preset, "list_registered_tools", lambda: [])
    monkeypatch.setattr(
        default_preset,
        "register_tool",
        lambda name, factory: registered_tools.append((name, factory)),
    )

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = get_default_agent(
        llm=llm,
        cli_mode=True,
        finish_tool_response_schema=_FinishResult,
    )

    assert registered_tools == [(FinishTool.__name__, FinishTool)]
    assert any(
        tool.name == "FinishTool" and tool.params == {"response_schema": _FinishResult}
        for tool in agent.tools
    )
    assert "FinishTool" not in agent.include_default_tools
    assert "ThinkTool" in agent.include_default_tools


def test_default_agent_uses_structured_finish_without_duplicate_warning(caplog):
    if FinishTool.__name__ not in list_registered_tools():
        register_tool(FinishTool.__name__, FinishTool)

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = get_default_agent(
        llm=llm,
        cli_mode=True,
        finish_tool_response_schema=_FinishResult,
    )

    assert any(
        tool.name == "FinishTool" and tool.params == {"response_schema": _FinishResult}
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
