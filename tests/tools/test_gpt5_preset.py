from openhands.sdk import LLM
from openhands.tools.preset.gpt5 import get_gpt5_agent, get_gpt5_tools


def test_get_gpt5_tools_includes_task_tracker_by_default() -> None:
    tools = get_gpt5_tools(enable_browser=False)

    assert [tool.name for tool in tools] == [
        "terminal",
        "apply_patch",
        "task_tracker",
    ]


def test_get_gpt5_agent_uses_gpt_5_4_prompt_template() -> None:
    agent = get_gpt5_agent(LLM(model="gpt-5", usage_id="test-llm"), cli_mode=True)

    assert agent.system_prompt_filename == "system_prompt_gpt_5_4.j2"
    assert "GPT-5.4-inspired coding workflow" in agent.static_system_message
    assert "task_tracker` tool" in agent.static_system_message
