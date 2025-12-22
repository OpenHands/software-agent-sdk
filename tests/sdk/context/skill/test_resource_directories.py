"""Tests for resource directories support (Issue #1477)."""

from pathlib import Path

from openhands.sdk.context.skills import (
    RESOURCE_DIRECTORIES,
    Skill,
    SkillResources,
    discover_skill_resources,
)


class TestSkillResources:
    """Tests for SkillResources model."""

    def test_has_resources_empty(self) -> None:
        """Should return False when no resources."""
        resources = SkillResources(skill_root="/path/to/skill")
        assert not resources.has_resources()

    def test_has_resources_with_scripts(self) -> None:
        """Should return True when scripts exist."""
        resources = SkillResources(
            skill_root="/path/to/skill",
            scripts=["run.sh"],
        )
        assert resources.has_resources()

    def test_has_resources_with_references(self) -> None:
        """Should return True when references exist."""
        resources = SkillResources(
            skill_root="/path/to/skill",
            references=["guide.md"],
        )
        assert resources.has_resources()

    def test_has_resources_with_assets(self) -> None:
        """Should return True when assets exist."""
        resources = SkillResources(
            skill_root="/path/to/skill",
            assets=["logo.png"],
        )
        assert resources.has_resources()

    def test_get_scripts_dir_exists(self, tmp_path: Path) -> None:
        """Should return scripts directory path when it exists."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()

        resources = SkillResources(skill_root=str(skill_dir))
        assert resources.get_scripts_dir() == scripts_dir

    def test_get_scripts_dir_not_exists(self, tmp_path: Path) -> None:
        """Should return None when scripts directory doesn't exist."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        resources = SkillResources(skill_root=str(skill_dir))
        assert resources.get_scripts_dir() is None

    def test_get_references_dir_exists(self, tmp_path: Path) -> None:
        """Should return references directory path when it exists."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()

        resources = SkillResources(skill_root=str(skill_dir))
        assert resources.get_references_dir() == refs_dir

    def test_get_assets_dir_exists(self, tmp_path: Path) -> None:
        """Should return assets directory path when it exists."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        assets_dir = skill_dir / "assets"
        assets_dir.mkdir()

        resources = SkillResources(skill_root=str(skill_dir))
        assert resources.get_assets_dir() == assets_dir


class TestDiscoverSkillResources:
    """Tests for discover_skill_resources() function."""

    def test_discovers_scripts(self, tmp_path: Path) -> None:
        """Should discover files in scripts/ directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.sh").write_text("#!/bin/bash")
        (scripts_dir / "setup.py").write_text("# setup")

        resources = discover_skill_resources(skill_dir)
        assert "run.sh" in resources.scripts
        assert "setup.py" in resources.scripts
        assert len(resources.scripts) == 2

    def test_discovers_references(self, tmp_path: Path) -> None:
        """Should discover files in references/ directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "guide.md").write_text("# Guide")
        (refs_dir / "api.md").write_text("# API")

        resources = discover_skill_resources(skill_dir)
        assert "guide.md" in resources.references
        assert "api.md" in resources.references
        assert len(resources.references) == 2

    def test_discovers_assets(self, tmp_path: Path) -> None:
        """Should discover files in assets/ directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        assets_dir = skill_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "logo.png").write_bytes(b"PNG")
        (assets_dir / "data.json").write_text("{}")

        resources = discover_skill_resources(skill_dir)
        assert "logo.png" in resources.assets
        assert "data.json" in resources.assets
        assert len(resources.assets) == 2

    def test_discovers_nested_files(self, tmp_path: Path) -> None:
        """Should discover files in nested directories."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        subdir = scripts_dir / "utils"
        subdir.mkdir()
        (subdir / "helper.py").write_text("# helper")

        resources = discover_skill_resources(skill_dir)
        # Should include relative path from scripts/
        assert "utils/helper.py" in resources.scripts

    def test_empty_when_no_resources(self, tmp_path: Path) -> None:
        """Should return empty lists when no resource directories."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        resources = discover_skill_resources(skill_dir)
        assert resources.scripts == []
        assert resources.references == []
        assert resources.assets == []
        assert not resources.has_resources()

    def test_sets_skill_root(self, tmp_path: Path) -> None:
        """Should set skill_root to absolute path."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        resources = discover_skill_resources(skill_dir)
        assert resources.skill_root == str(skill_dir.resolve())

    def test_files_are_sorted(self, tmp_path: Path) -> None:
        """Should return files in sorted order."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "z_last.sh").write_text("")
        (scripts_dir / "a_first.sh").write_text("")
        (scripts_dir / "m_middle.sh").write_text("")

        resources = discover_skill_resources(skill_dir)
        assert resources.scripts == ["a_first.sh", "m_middle.sh", "z_last.sh"]


class TestResourceDirectoriesConstant:
    """Tests for RESOURCE_DIRECTORIES constant."""

    def test_contains_expected_directories(self) -> None:
        """Should contain scripts, references, and assets."""
        assert "scripts" in RESOURCE_DIRECTORIES
        assert "references" in RESOURCE_DIRECTORIES
        assert "assets" in RESOURCE_DIRECTORIES
        assert len(RESOURCE_DIRECTORIES) == 3


class TestSkillLoadWithResources:
    """Tests for Skill.load() with resource directories."""

    def test_loads_resources_from_skill_directory(self, tmp_path: Path) -> None:
        """Should discover resources when loading SKILL.md."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        # Create SKILL.md
        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
triggers:
  - test
---
# My Skill
"""
        )

        # Create resource directories
        scripts_dir = my_skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.sh").write_text("#!/bin/bash")

        refs_dir = my_skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "guide.md").write_text("# Guide")

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.resources is not None
        assert "run.sh" in skill.resources.scripts
        assert "guide.md" in skill.resources.references

    def test_no_resources_for_flat_skills(self, tmp_path: Path) -> None:
        """Should not discover resources for flat .md files."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        # Create flat skill file
        skill_md = skill_dir / "flat-skill.md"
        skill_md.write_text(
            """---
triggers:
  - test
---
# Flat Skill
"""
        )

        # Create scripts dir in skills dir (should be ignored)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.sh").write_text("#!/bin/bash")

        skill = Skill.load(skill_md, skill_dir)
        assert skill.resources is None

    def test_resources_none_when_empty(self, tmp_path: Path) -> None:
        """Should set resources to None when no resource directories exist."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill")

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.resources is None

    def test_resources_with_all_types(self, tmp_path: Path) -> None:
        """Should discover all resource types."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill")

        # Create all resource directories
        for dir_name in RESOURCE_DIRECTORIES:
            res_dir = my_skill_dir / dir_name
            res_dir.mkdir()
            (res_dir / "file.txt").write_text("content")

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        assert skill.resources is not None
        assert skill.resources.scripts == ["file.txt"]
        assert skill.resources.references == ["file.txt"]
        assert skill.resources.assets == ["file.txt"]

    def test_resources_serialization(self, tmp_path: Path) -> None:
        """Should serialize resources correctly."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        my_skill_dir = skill_dir / "my-skill"
        my_skill_dir.mkdir()

        skill_md = my_skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill")

        scripts_dir = my_skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.sh").write_text("#!/bin/bash")

        skill = Skill.load(skill_md, skill_dir, directory_name="my-skill")
        data = skill.model_dump()

        assert "resources" in data
        assert data["resources"]["scripts"] == ["run.sh"]
        assert data["resources"]["skill_root"] == str(my_skill_dir.resolve())
