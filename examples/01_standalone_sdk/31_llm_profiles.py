"""Create and use an LLM profile with :class:`LLMRegistry`.

Run with::

    uv run python examples/01_standalone_sdk/31_llm_profiles.py

Profiles are stored under ``~/.openhands/llm-profiles/<name>.json`` by default.
Set ``LLM_PROFILE_NAME`` to pick a profile and ``LLM_API_KEY`` to supply
credentials when the profile omits secrets.
"""

import json
import os
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    LLMRegistry,
    Tool,
)
from openhands.tools.terminal import TerminalTool


PROFILE_NAME = os.getenv("LLM_PROFILE_NAME", "gpt-5-mini")


def ensure_profile_exists(registry: LLMRegistry, name: str) -> None:
    """Create a starter profile in the default directory when missing."""

    if name in registry.list_profiles():
        return

    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    profile_defaults = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        temperature=0.2,
        max_output_tokens=4096,
    )
    path = registry.save_profile(name, profile_defaults)
    print(f"Created profile '{name}' at {path}")


def load_profile(registry: LLMRegistry, name: str) -> LLM:
    llm = registry.load_profile(name)
    if llm.api_key is None:
        api_key = os.getenv("LLM_API_KEY")
        if api_key is None:
            raise RuntimeError(
                "Set LLM_API_KEY to authenticate, or save the profile with "
                "include_secrets=True."
            )
        llm = llm.model_copy(update={"api_key": SecretStr(api_key)})
    return llm


def main() -> None:
    registry = LLMRegistry()
    ensure_profile_exists(registry, PROFILE_NAME)

    llm = load_profile(registry, PROFILE_NAME)

    tools = [Tool(name=TerminalTool.name)]
    agent = Agent(llm=llm, tools=tools)

    workspace_dir = Path(os.getcwd())
    summary_path = workspace_dir / "summary_readme.md"
    if summary_path.exists():
        summary_path.unlink()

    persistence_root = workspace_dir / ".conversations_llm_profiles"
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace_dir),
        persistence_dir=str(persistence_root),
        visualizer=None,
    )

    conversation.send_message(
        "Read README.md in this workspace, create a concise summary in "
        "summary_readme.md (overwrite it if it exists), and respond with "
        "SUMMARY_READY when the file is written."
    )
    conversation.run()

    if summary_path.exists():
        print(f"summary_readme.md written to {summary_path}")
    else:
        print("summary_readme.md not found after first run")

    conversation.send_message(
        "Thanks! Delete summary_readme.md from the workspace and respond with "
        "SUMMARY_REMOVED once it is gone."
    )
    conversation.run()

    if summary_path.exists():
        print("summary_readme.md still present after deletion request")
    else:
        print("summary_readme.md removed")

    persistence_dir = conversation.state.persistence_dir
    if persistence_dir is None:
        raise RuntimeError("Conversation did not persist base state to disk")

    base_state_path = Path(persistence_dir) / "base_state.json"
    state_payload = json.loads(base_state_path.read_text())
    llm_entry = state_payload.get("agent", {}).get("llm", {})
    profile_in_state = llm_entry.get("profile_id")
    print(f"Profile recorded in base_state.json: {profile_in_state}")
    if profile_in_state != PROFILE_NAME:
        print(
            "Warning: profile_id in base_state.json does not match the profile "
            "used at runtime."
        )


if __name__ == "__main__":  # pragma: no cover
    main()
