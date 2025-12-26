"""Example on how to load and save LLM profiles using the :class:`LLMRegistry`.

Run with::
    uv run python examples/01_standalone_sdk/33_llm_profiles.py

Profiles are stored under ``~/.openhands/llm-profiles/<name>.json`` by default.
"""

import os
from pathlib import Path
from typing import Final

from openhands.sdk import (
    LLM,
    Agent,
    LLMRegistry,
    Tool,
)
from openhands.tools.terminal import TerminalTool


PROFILE_NAME: Final[str] = os.getenv("LLM_PROFILE_NAME", "gpt-5-mini")


def _get_llm_profile() -> LLM:
    return LLM(
        usage_id="new_task_agent",
        model="gemini-flash",
        api_key=None,
    )


def main() -> None:
    # initialize the registry with the profile store
    registry = LLMRegistry(
        load_from_disk=True,
        profile_manager_path=Path.cwd() / ".openhands/profiles",
    )

    # we can check the available profiles by usage id
    print(f"Available profiles by usage id: {registry.list_usage_ids()}")

    # and load our preferred profile for the
    # task we want to accomplish
    code_llm = registry.get("claude_agent")
    code_agent = Agent(llm=code_llm, tools=[Tool(name=TerminalTool.name)])  # noqa: F841

    summary_llm = registry.get("gpt_agent")
    summary_agent = Agent(llm=summary_llm, tools=[Tool(name=TerminalTool.name)])  # noqa: F841

    # For a new task, we want to define a new LLM instance.
    # To use it in later experiments as well, we can simply
    # add it to the registry.
    new_task_llm = _get_llm_profile()
    registry.add(new_task_llm)
    new_task_agent = Agent(llm=new_task_llm, tools=[Tool(name=TerminalTool.name)])  # noqa: F841

    # At the end, we can save the registry so that every new LLM instance
    # will be available in the next session.
    registry.export_profiles()
    print(f"Available profiles by usage id: {registry.list_usage_ids()}")
    print("All done!")


if __name__ == "__main__":  # pragma: no cover
    main()
