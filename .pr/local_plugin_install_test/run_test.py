"""Generate a small persisted conversation as a smoke test.

This script is meant to be run manually during PR development.
The output artifacts under this folder are committed temporarily for review and
will be auto-cleaned by the repo workflow when the PR is approved.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from openhands.sdk import Agent, Conversation
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.plugin.installed import install_plugin, load_installed_plugins
from openhands.sdk.testing import TestLLM


def _write_example_plugin(plugin_dir: Path, *, version: str) -> None:
    (plugin_dir / ".plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "local-plugin",
                "version": version,
                "description": "Example local plugin (PR smoke test)",
            }
        )
    )

    skill_dir = plugin_dir / "skills" / "hello"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: hello
description: Say hello
triggers:
  - hello
---

Reply with a short greeting.
"""
    )


def main() -> None:
    root = Path(__file__).parent
    plugin_src = root / "plugin_src" / "local-plugin"
    installed_dir = root / "installed_root" / "plugins" / "installed"
    persistence_root = root / "persistence"

    plugin_src.mkdir(parents=True, exist_ok=True)
    _write_example_plugin(plugin_src, version="1.0.0")

    installed_dir.mkdir(parents=True, exist_ok=True)

    install_plugin(source=str(plugin_src), installed_dir=installed_dir, force=True)

    plugins = load_installed_plugins(installed_dir=installed_dir)
    assert len(plugins) == 1

    # Merge plugin skills into agent context.
    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="Done")])]
        ),
        tools=[],
    )

    merged_context = None
    for plugin in plugins:
        merged_context = plugin.add_skills_to(merged_context)

    agent = agent.model_copy(update={"agent_context": merged_context})

    conversation_id = uuid.UUID("00000000-0000-0000-0000-000000000203")
    conversation = Conversation(
        agent=agent,
        persistence_dir=persistence_root,
        conversation_id=conversation_id,
        workspace=str(root),
    )

    conversation.send_message("hello")
    conversation.run()

    persistence_dir = Path(conversation.state.persistence_dir or "")
    assert persistence_dir.exists(), "Persistence dir not created"

    print(f"Wrote persistence to: {persistence_dir}")


if __name__ == "__main__":
    main()
