"""Example: Uninstall Installed Skills

This example demonstrates the uninstall lifecycle for skills:

1. Install multiple skills into an isolated installed directory
2. Inspect the `.installed.json` metadata file before uninstalling
3. Uninstall one skill by name
4. Confirm the skill directory and metadata entry are removed while other
   installed skills remain available
"""

import json
import tempfile
from pathlib import Path

from openhands.sdk.skills import install_skill, list_installed_skills, uninstall_skill


script_dir = Path(__file__).resolve().parent
skills_dir = script_dir.parent / "01_loading_agentskills" / "example_skills"


def print_state(label: str, installed_dir: Path) -> None:
    """Print tracked skills and the persisted metadata file."""
    print(f"\n{label}")
    print("-" * len(label))

    installed = list_installed_skills(installed_dir=installed_dir)
    print(f"Tracked skills: {[info.name for info in installed]}")

    metadata_path = installed_dir / ".installed.json"
    print("Metadata file:")
    print(metadata_path.read_text())


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        installed_dir = Path(tmpdir) / "installed-skills"
        installed_dir.mkdir(parents=True)

        install_skill(
            source=str(skills_dir / "rot13-encryption"),
            installed_dir=installed_dir,
        )
        install_skill(
            source=str(skills_dir / "code-style-guide"),
            installed_dir=installed_dir,
        )
        print_state("Before uninstall", installed_dir)

        assert uninstall_skill("rot13-encryption", installed_dir=installed_dir) is True
        print_state("After uninstall", installed_dir)

        assert not (installed_dir / "rot13-encryption").exists()
        metadata = json.loads((installed_dir / ".installed.json").read_text())
        assert "rot13-encryption" not in metadata["skills"]
        assert "code-style-guide" in metadata["skills"]

    print("\nEXAMPLE_COST: 0")
