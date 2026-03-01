"""Example: Install + manage a local plugin (no LLM required).

This demonstrates the installed plugins utilities introduced in this PR.

Key ideas:
- Installed plugin packages live under `~/.openhands/plugins/installed/` by default.
- Each plugin is a self-contained directory that can include `skills/`, `agents/`,
  `hooks/`, `.mcp.json`, etc. (Claude Code style).

For this example we avoid writing to the real home directory by passing a
temporary `installed_dir=`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from openhands.sdk.plugin import (
    get_installed_plugin,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)


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
---

Say hello.
"""
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Create a local plugin directory (this simulates a repo checkout).
        plugin_source_dir = tmp_path / "local-plugin"
        _write_example_plugin(plugin_source_dir, version="1.0.0")

        # Use a temp install dir instead of ~/.openhands/plugins/installed/
        installed_dir = tmp_path / "plugins" / "installed"

        info = install_plugin(
            source=str(plugin_source_dir), installed_dir=installed_dir
        )
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

        # Update: mutate the local plugin source and call update_plugin(), which
        # reinstalls from the original source with ref=None (latest).
        _write_example_plugin(plugin_source_dir, version="1.0.1")
        updated = update_plugin("local-plugin", installed_dir=installed_dir)
        assert updated is not None
        print(f"\nUpdated: {updated.name} v{updated.version}")

        uninstall_plugin("local-plugin", installed_dir=installed_dir)
        print("\nAfter uninstall:")
        print(list_installed_plugins(installed_dir=installed_dir))

    print("EXAMPLE_COST: 0")


if __name__ == "__main__":
    main()
