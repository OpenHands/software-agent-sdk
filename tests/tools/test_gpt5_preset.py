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
    assert (
        "Persist until the task is fully handled end-to-end"
        in agent.static_system_message
    )
    assert "task_tracker` tool" in agent.static_system_message


def test_get_gpt5_agent_only_mentions_same_machine_in_cli_mode() -> None:
    cli_agent = get_gpt5_agent(LLM(model="gpt-5", usage_id="cli-llm"), cli_mode=True)
    remote_agent = get_gpt5_agent(
        LLM(model="gpt-5", usage_id="remote-llm"),
        cli_mode=False,
    )

    assert "When running in CLI mode, the user is on the same machine" in (
        cli_agent.static_system_message
    )
    assert "When running in CLI mode, the user is on the same machine" not in (
        remote_agent.static_system_message
    )
