"""Example: Installing and Managing Skills

This example demonstrates installed skill lifecycle operations in the SDK:

1. Install skills from local paths into persistent storage
2. List tracked skills and load only the enabled ones
3. Inspect the `.installed.json` metadata file and `enabled` flag
4. Disable and re-enable a skill without reinstalling it
5. Uninstall a skill while leaving other installed skills available

For marketplace installation flows, see:
`examples/01_standalone_sdk/43_mixed_marketplace_skills/`.
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
    uninstall_skill,
)


script_dir = Path(__file__).resolve().parent
example_skills_dir = script_dir.parent / "01_loading_agentskills" / "example_skills"


def print_state(label: str, installed_dir: Path) -> None:
    """Print tracked, loaded, and persisted skill state."""
    print(f"\n{label}")
    print("-" * len(label))

    installed = list_installed_skills(installed_dir=installed_dir)
    print("Tracked skills:")
    for info in installed:
        print(f"  - {info.name} (enabled={info.enabled}, source={info.source})")

    loaded = load_installed_skills(installed_dir=installed_dir)
    print(f"Loaded skills: {[skill.name for skill in loaded]}")

    metadata = json.loads((installed_dir / ".installed.json").read_text())
    print("Metadata file:")
    print(json.dumps(metadata, indent=2))


def demo_install_skills(installed_dir: Path) -> list[str]:
    """Install the sample skills into the isolated installed directory."""
    print("\n" + "=" * 60)
    print("DEMO 1: Installing local skills")
    print("=" * 60)

    installed_names: list[str] = []
    for skill_dir in sorted(example_skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        info = install_skill(source=str(skill_dir), installed_dir=installed_dir)
        installed_names.append(info.name)
        print(f"✓ Installed: {info.name}")
        print(f"  Source: {info.source}")
        print(f"  Path: {info.install_path}")

    return installed_names


def demo_list_and_load_skills(installed_dir: Path) -> None:
    """List tracked skills and load them as runtime Skill objects."""
    print("\n" + "=" * 60)
    print("DEMO 2: Listing and loading installed skills")
    print("=" * 60)

    installed = list_installed_skills(installed_dir=installed_dir)
    print("Tracked skills:")
    for info in installed:
        desc = (info.description or "No description")[:60]
        print(f"  - {info.name} (enabled={info.enabled})")
        print(f"    Description: {desc}...")

    loaded = load_installed_skills(installed_dir=installed_dir)
    print(f"\nLoaded {len(loaded)} skill(s):")
    for skill in loaded:
        desc = (skill.description or "No description")[:60]
        print(f"  - {skill.name}: {desc}...")


def demo_enable_disable_skill(installed_dir: Path, skill_name: str) -> None:
    """Disable then re-enable a skill and show the persisted metadata."""
    print("\n" + "=" * 60)
    print("DEMO 3: Disabling and re-enabling a skill")
    print("=" * 60)

    print_state("Before disable", installed_dir)

    assert disable_skill(skill_name, installed_dir=installed_dir) is True
    print_state("After disable", installed_dir)
    assert skill_name not in [
        skill.name for skill in load_installed_skills(installed_dir=installed_dir)
    ]

    metadata = json.loads((installed_dir / ".installed.json").read_text())
    assert metadata["skills"][skill_name]["enabled"] is False

    assert enable_skill(skill_name, installed_dir=installed_dir) is True
    print_state("After re-enable", installed_dir)

    metadata = json.loads((installed_dir / ".installed.json").read_text())
    assert metadata["skills"][skill_name]["enabled"] is True
    assert skill_name in [
        skill.name for skill in load_installed_skills(installed_dir=installed_dir)
    ]


def demo_uninstall_skill(
    installed_dir: Path, skill_name: str, remaining_skill_name: str
) -> None:
    """Uninstall one skill and confirm the other skill remains available."""
    print("\n" + "=" * 60)
    print("DEMO 4: Uninstalling a skill")
    print("=" * 60)

    assert uninstall_skill(skill_name, installed_dir=installed_dir) is True
    print_state("After uninstall", installed_dir)

    assert not (installed_dir / skill_name).exists()
    metadata = json.loads((installed_dir / ".installed.json").read_text())
    assert skill_name not in metadata["skills"]
    assert remaining_skill_name in metadata["skills"]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        installed_dir = Path(tmpdir) / "installed-skills"
        installed_dir.mkdir(parents=True)

        installed_names = demo_install_skills(installed_dir)
        demo_list_and_load_skills(installed_dir)
        demo_enable_disable_skill(installed_dir, skill_name="rot13-encryption")
        demo_uninstall_skill(
            installed_dir,
            skill_name="rot13-encryption",
            remaining_skill_name="code-style-guide",
        )

        remaining_names = [
            info.name for info in list_installed_skills(installed_dir=installed_dir)
        ]
        assert remaining_names == ["code-style-guide"]
        assert sorted(installed_names) == ["code-style-guide", "rot13-encryption"]

    print("\nEXAMPLE_COST: 0")
