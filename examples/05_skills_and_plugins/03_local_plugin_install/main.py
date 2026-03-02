"""Example: Install + manage a local plugin (no external LLM required).

This example demonstrates the *installed plugins* utilities introduced in this PR.

Key ideas:
- Installed plugin packages live under `~/.openhands/plugins/installed/` by default.
- Each plugin is a self-contained directory that can include `skills/`, `agents/`,
  `hooks/`, `.mcp.json`, etc. (Claude Code style).

By default this example uses a temporary directory and leaves no artifacts.

To write artifacts to disk (useful for PR review), set:

```bash
export OPENHANDS_EXAMPLE_ARTIFACT_DIR=.pr/local_plugin_install_test
```

Then run:

```bash
uv run python examples/05_skills_and_plugins/03_local_plugin_install/main.py
```

This will create (and overwrite) `plugin_src/`, `installed_root/`, and
`persistence/` under the artifact directory.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path

from openhands.sdk import Agent, Conversation
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.plugin import (
    get_installed_plugin,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)
from openhands.sdk.testing import TestLLM


def _write_example_plugin(plugin_dir: Path, *, version: str) -> None:
    (plugin_dir / ".plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "local-plugin",
                "version": version,
                "description": "Example local plugin",
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


artifact_dir = os.getenv("OPENHANDS_EXAMPLE_ARTIFACT_DIR")

with ExitStack() as stack:
    if artifact_dir:
        root = Path(artifact_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)

        for subdir in ("plugin_src", "installed_root", "persistence"):
            shutil.rmtree(root / subdir, ignore_errors=True)
    else:
        tmp_dir = stack.enter_context(tempfile.TemporaryDirectory())
        root = Path(tmp_dir)

    # Create a local plugin directory (this simulates a repo checkout).
    plugin_source_dir = root / "plugin_src" / "local-plugin"
    _write_example_plugin(plugin_source_dir, version="1.0.0")

    # Install into a dedicated root (avoids touching real ~/.openhands/).
    installed_dir = root / "installed_root" / "plugins" / "installed"

    info = install_plugin(source=str(plugin_source_dir), installed_dir=installed_dir)
    print(f"Installed: {info.name} v{info.version} from {info.source}")

    print("\nList installed plugins:")
    for item in list_installed_plugins(installed_dir=installed_dir):
        print(f"- {item.name} v{item.version} ({item.source})")

    print("\nLoad installed plugins:")
    plugins = load_installed_plugins(installed_dir=installed_dir)
    for plugin in plugins:
        print(f"- {plugin.name}: {len(plugin.get_all_skills())} skill(s)")

    print("\nGet installed plugin:")
    print(get_installed_plugin("local-plugin", installed_dir=installed_dir))

    # Smoke test: run a minimal Conversation with persistence enabled.
    #
    # We use TestLLM so this runs without external services. The plugin skill
    # is triggered by the user message "hello".
    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="Done")])]
        ),
        tools=[],
    )

    merged_context = agent.agent_context
    for plugin in plugins:
        merged_context = plugin.add_skills_to(merged_context)
    agent = agent.model_copy(update={"agent_context": merged_context})

    persistence_dir = root / "persistence"
    conversation_id = (
        uuid.UUID("00000000-0000-0000-0000-000000000203")
        if artifact_dir
        else uuid.uuid4()
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(root),
        persistence_dir=persistence_dir,
        conversation_id=conversation_id,
        visualizer=None,
    )
    conversation.send_message("hello")
    conversation.run()

    print(f"\nActivated skills: {conversation.state.activated_knowledge_skills}")
    print(f"Wrote persistence to: {conversation.state.persistence_dir}")

    # Don't leave transient lock files in persisted artifacts.
    if conversation.state.persistence_dir:
        lockfile = (
            Path(conversation.state.persistence_dir) / "events" / ".eventlog.lock"
        )
        lockfile.unlink(missing_ok=True)

    # Update: mutate the local plugin source and call update_plugin(), which
    # reinstalls from the original source with ref=None (latest).
    _write_example_plugin(plugin_source_dir, version="1.0.1")
    updated = update_plugin("local-plugin", installed_dir=installed_dir)
    assert updated is not None
    print(f"\nUpdated: {updated.name} v{updated.version}")

    if artifact_dir:
        print("\nSkipping uninstall (artifact mode)")
    else:
        uninstall_plugin("local-plugin", installed_dir=installed_dir)
        print("\nAfter uninstall:")
        print(list_installed_plugins(installed_dir=installed_dir))

print("EXAMPLE_COST: 0")
