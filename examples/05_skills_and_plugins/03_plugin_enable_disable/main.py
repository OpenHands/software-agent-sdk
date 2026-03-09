"""Example: Enable and Disable Installed Plugins

This example demonstrates the persistent plugin lifecycle APIs:

1. Install a plugin into an isolated installed directory
2. Inspect the `.installed.json` metadata file and `enabled` flag
3. Disable the plugin and confirm it is excluded from `load_installed_plugins()`
4. Re-enable the plugin without reinstalling it

This is useful for CLI commands that need to toggle plugins on and off while
preserving the installation on disk.
"""

import json
import tempfile
from pathlib import Path

from openhands.sdk.plugin import (
    disable_plugin,
    enable_plugin,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
)


script_dir = Path(__file__).resolve().parent
local_plugin_path = (
    script_dir.parent / "02_loading_plugins" / "example_plugins" / "code-quality"
)


def print_state(label: str, installed_dir: Path) -> None:
    """Print tracked, loaded, and persisted plugin state."""
    print(f"\n{label}")
    print("-" * len(label))

    installed = list_installed_plugins(installed_dir=installed_dir)
    print("Tracked plugins:")
    for info in installed:
        print(f"  - {info.name} (enabled={info.enabled})")

    loaded = load_installed_plugins(installed_dir=installed_dir)
    print(f"Loaded plugins: {[plugin.name for plugin in loaded]}")

    metadata_path = installed_dir / ".installed.json"
    print("Metadata file:")
    print(metadata_path.read_text())


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        installed_dir = Path(tmpdir) / "installed-plugins"
        installed_dir.mkdir(parents=True)

        info = install_plugin(
            source=str(local_plugin_path),
            installed_dir=installed_dir,
        )
        print(f"Installed plugin: {info.name} v{info.version}")
        print_state("After install", installed_dir)

        assert disable_plugin(info.name, installed_dir=installed_dir) is True
        print_state("After disable", installed_dir)

        metadata = json.loads((installed_dir / ".installed.json").read_text())
        assert metadata["plugins"][info.name]["enabled"] is False
        assert load_installed_plugins(installed_dir=installed_dir) == []

        assert enable_plugin(info.name, installed_dir=installed_dir) is True
        print_state("After re-enable", installed_dir)

        metadata = json.loads((installed_dir / ".installed.json").read_text())
        assert metadata["plugins"][info.name]["enabled"] is True
        loaded_names = [plugin.name for plugin in load_installed_plugins(installed_dir)]
        assert loaded_names == [info.name]

    print("\nEXAMPLE_COST: 0")
