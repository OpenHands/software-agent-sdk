"""Example: Enable and Disable Installed Skills

This example demonstrates the persistent skill lifecycle APIs:

1. Install a skill into an isolated installed directory
2. Inspect the `.installed.json` metadata file and `enabled` flag
3. Disable the skill and confirm it is excluded from `load_installed_skills()`
4. Re-enable the skill without reinstalling it

This is useful for CLI commands that need to toggle skills on and off while
preserving the installation on disk.
"""

import json
import tempfile
from pathlib import Path

from openhands.sdk.skills import (
    disable_skill,
    enable_skill,
    install_skill,
    list_installed_skills,
    load_installed_skills,
)


script_dir = Path(__file__).resolve().parent
local_skill_path = (
    script_dir.parent / "01_loading_agentskills" / "example_skills" / "rot13-encryption"
)


def print_state(label: str, installed_dir: Path) -> None:
    """Print tracked, loaded, and persisted skill state."""
    print(f"\n{label}")
    print("-" * len(label))

    installed = list_installed_skills(installed_dir=installed_dir)
    print("Tracked skills:")
    for info in installed:
        print(f"  - {info.name} (enabled={info.enabled})")

    loaded = load_installed_skills(installed_dir=installed_dir)
    print(f"Loaded skills: {[skill.name for skill in loaded]}")

    metadata_path = installed_dir / ".installed.json"
    print("Metadata file:")
    print(metadata_path.read_text())


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        installed_dir = Path(tmpdir) / "installed-skills"
        installed_dir.mkdir(parents=True)

        info = install_skill(
            source=str(local_skill_path),
            installed_dir=installed_dir,
        )
        print(f"Installed skill: {info.name}")
        print_state("After install", installed_dir)

        assert disable_skill(info.name, installed_dir=installed_dir) is True
        print_state("After disable", installed_dir)

        metadata = json.loads((installed_dir / ".installed.json").read_text())
        assert metadata["skills"][info.name]["enabled"] is False
        assert load_installed_skills(installed_dir=installed_dir) == []

        assert enable_skill(info.name, installed_dir=installed_dir) is True
        print_state("After re-enable", installed_dir)

        metadata = json.loads((installed_dir / ".installed.json").read_text())
        assert metadata["skills"][info.name]["enabled"] is True
        loaded_names = [skill.name for skill in load_installed_skills(installed_dir)]
        assert loaded_names == [info.name]

    print("\nEXAMPLE_COST: 0")
