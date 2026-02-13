"""Tests for resource directories support (Issue #1477)."""

from pathlib import Path

from openhands.sdk.context.skills import (
    RESOURCE_DIRECTORIES,
    Skill,
    SkillResources,
    discover_skill_resources,
)


def test_skill_resources_model(tmp_path: Path) -> None:
    """SkillResources should track resources and provide directory paths."""
    # Empty resources
    resources = SkillResources(skill_root="/path/to/skill")
    assert not resources.has_resources()

    # With resources
    resources = SkillResources(skill_root="/path", scripts=["run.sh"])
    assert resources.has_resources()

    # Directory path getters
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "scripts").mkdir()
    resources = SkillResources(skill_root=str(skill_dir))
    assert resources.get_scripts_dir() == skill_dir / "scripts"
    assert resources.get_references_dir() is None  # Doesn't exist


def test_discover_skill_resources(tmp_path: Path) -> None:
    """discover_skill_resources() should find files in resource directories."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()

    # Create resource directories with files
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.sh").write_text("#!/bin/bash")
    subdir = scripts_dir / "utils"
    subdir.mkdir()
    (subdir / "helper.py").write_text("# helper")

    refs_dir = skill_dir / "references"
    refs_dir.mkdir()
    (refs_dir / "guide.md").write_text("# Guide")

    resources = discover_skill_resources(skill_dir)
    assert "run.sh" in resources.scripts
    assert "utils/helper.py" in resources.scripts  # Nested files
    assert "guide.md" in resources.references
    assert resources.assets == []  # No assets dir
    assert resources.skill_root == str(skill_dir.resolve())


def test_resource_directories_constant() -> None:
    """RESOURCE_DIRECTORIES should contain standard directory names."""
    assert set(RESOURCE_DIRECTORIES) == {"scripts", "references", "assets"}


def test_skill_load_with_resources(tmp_path: Path) -> None:
    """Skill.load() should discover resources for SKILL.md directories."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    my_skill_dir = skill_dir / "my-skill"
    my_skill_dir.mkdir()

    (my_skill_dir / "SKILL.md").write_text("# My Skill")
    scripts_dir = my_skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.sh").write_text("#!/bin/bash")

    # SKILL.md directory format - should have resources (auto-detects directory name)
    skill = Skill.load(my_skill_dir / "SKILL.md", skill_dir)
    assert skill.resources is not None
    assert "run.sh" in skill.resources.scripts

    # Flat file format - should not have resources
    flat_skill = skill_dir / "flat.md"
    flat_skill.write_text("# Flat Skill")
    skill = Skill.load(flat_skill, skill_dir)
    assert skill.resources is None
