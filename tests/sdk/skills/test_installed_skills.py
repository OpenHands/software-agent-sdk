"""Tests for installed skills management."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.skills import (
    InstalledSkillsMetadata,
    get_installed_skill,
    get_installed_skills_dir,
    install_skill,
    list_installed_skills,
    load_installed_skills,
    uninstall_skill,
    update_skill,
)


def _create_skill_dir(
    base_dir: Path,
    dir_name: str,
    *,
    frontmatter_name: str | None = None,
    description: str = "A test skill",
) -> Path:
    skill_dir = base_dir / dir_name
    skill_dir.mkdir(parents=True)
    name = frontmatter_name or dir_name
    skill_md = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
    (skill_dir / "SKILL.md").write_text(skill_md)
    return skill_dir


@pytest.fixture
def installed_dir(tmp_path: Path) -> Path:
    installed = tmp_path / "installed"
    installed.mkdir(parents=True)
    return installed


@pytest.fixture
def sample_skill_dir(tmp_path: Path) -> Path:
    return _create_skill_dir(tmp_path, "sample-skill")


def test_get_installed_skills_dir_returns_default_path() -> None:
    path = get_installed_skills_dir()
    assert ".openhands" in str(path)
    assert "skills" in str(path)
    assert "installed" in str(path)


def test_install_from_local_path(sample_skill_dir: Path, installed_dir: Path) -> None:
    info = install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    assert info.name == "sample-skill"
    assert info.source == str(sample_skill_dir)
    assert info.description == "A test skill"

    skill_path = installed_dir / "sample-skill"
    assert skill_path.exists()
    assert (skill_path / "SKILL.md").exists()

    metadata = InstalledSkillsMetadata.load_from_dir(installed_dir)
    assert "sample-skill" in metadata.skills


def test_install_already_exists_raises_error(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    with pytest.raises(FileExistsError, match="already installed"):
        install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)


def test_install_with_force_overwrites(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    marker_file = installed_dir / "sample-skill" / "marker.txt"
    marker_file.write_text("original")

    install_skill(
        source=str(sample_skill_dir),
        installed_dir=installed_dir,
        force=True,
    )

    assert not marker_file.exists()


def test_install_invalid_skill_name_raises_error(
    tmp_path: Path, installed_dir: Path
) -> None:
    invalid_skill_dir = _create_skill_dir(
        tmp_path,
        "bad-skill",
        frontmatter_name="Bad_Name",
    )

    with pytest.raises(SkillValidationError):
        install_skill(source=str(invalid_skill_dir), installed_dir=installed_dir)


def test_uninstall_existing_skill(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    assert uninstall_skill("sample-skill", installed_dir=installed_dir) is True
    assert not (installed_dir / "sample-skill").exists()


def test_list_empty_directory(installed_dir: Path) -> None:
    assert list_installed_skills(installed_dir=installed_dir) == []


def test_list_installed_skills(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    skills = list_installed_skills(installed_dir=installed_dir)

    assert len(skills) == 1
    assert skills[0].name == "sample-skill"


def test_list_discovers_untracked_skills(installed_dir: Path) -> None:
    _create_skill_dir(installed_dir, "manual-skill")

    skills = list_installed_skills(installed_dir=installed_dir)

    assert len(skills) == 1
    assert skills[0].name == "manual-skill"
    assert skills[0].source == "local"


def test_list_cleans_up_missing_skills(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    shutil.rmtree(installed_dir / "sample-skill")

    skills = list_installed_skills(installed_dir=installed_dir)

    assert skills == []
    metadata = InstalledSkillsMetadata.load_from_dir(installed_dir)
    assert "sample-skill" not in metadata.skills


def test_load_empty_directory(installed_dir: Path) -> None:
    assert load_installed_skills(installed_dir=installed_dir) == []


def test_load_installed_skills(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    skills = load_installed_skills(installed_dir=installed_dir)

    assert len(skills) == 1
    assert skills[0].name == "sample-skill"


def test_get_installed_skill_returns_info(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    info = get_installed_skill("sample-skill", installed_dir=installed_dir)

    assert info is not None
    assert info.name == "sample-skill"


def test_update_skill_reinstalls_from_source(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    updated = (
        "---\n"
        "name: sample-skill\n"
        "description: Updated description\n"
        "---\n"
        "# sample-skill\n"
    )
    (sample_skill_dir / "SKILL.md").write_text(updated)

    info = update_skill("sample-skill", installed_dir=installed_dir)

    assert info is not None
    assert info.description == "Updated description"
    installed_content = (installed_dir / "sample-skill" / "SKILL.md").read_text()
    assert "Updated description" in installed_content


def test_metadata_invalid_json_returns_empty(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    installed.mkdir()
    metadata_path = installed / ".installed.json"
    metadata_path.write_text("invalid json {")

    metadata = InstalledSkillsMetadata.load_from_dir(installed)

    assert metadata.skills == {}


# --- Tests for install_skills_from_marketplace ---


def _create_marketplace(
    base_dir: Path,
    skills: list[dict[str, str]],
    plugins: list[dict[str, str]] | None = None,
) -> Path:
    """Helper to create a marketplace directory with skills and optional plugins."""
    marketplace_dir = base_dir / "marketplace"
    marketplace_dir.mkdir(parents=True)

    plugin_dir = marketplace_dir / ".plugin"
    plugin_dir.mkdir()

    import json

    manifest = {
        "name": "test-marketplace",
        "owner": {"name": "Test"},
        "skills": skills,
        "plugins": plugins or [],
    }
    (plugin_dir / "marketplace.json").write_text(json.dumps(manifest))

    return marketplace_dir


class TestInstallSkillsFromMarketplace:
    """Tests for install_skills_from_marketplace function."""

    def test_install_local_skills(self, tmp_path: Path) -> None:
        """Test installing local skills from marketplace."""
        from openhands.sdk.skills import install_skills_from_marketplace

        # Create marketplace with local skill
        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "my-skill", "source": "./skills/my-skill"}],
        )

        # Create the local skill
        skill_dir = marketplace_dir / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Test\n---\n# my-skill"
        )

        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir
        )

        assert len(installed) == 1
        assert installed[0].name == "my-skill"
        assert (installed_dir / "my-skill" / "SKILL.md").exists()

    def test_install_skills_force_overwrite(self, tmp_path: Path) -> None:
        """Test force reinstalling existing skills."""
        from openhands.sdk.skills import install_skills_from_marketplace

        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "my-skill", "source": "./skills/my-skill"}],
        )

        skill_dir = marketplace_dir / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Original\n---\n# my-skill"
        )

        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        # First install
        install_skills_from_marketplace(marketplace_dir, installed_dir=installed_dir)

        # Update skill content
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Updated\n---\n# my-skill"
        )

        # Reinstall without force - should not update
        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir, force=False
        )
        assert len(installed) == 0  # Already exists, not reinstalled

        # Reinstall with force
        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir, force=True
        )
        assert len(installed) == 1
        content = (installed_dir / "my-skill" / "SKILL.md").read_text()
        assert "Updated" in content

    def test_install_handles_missing_skill_source(self, tmp_path: Path) -> None:
        """Test that missing skill sources are skipped gracefully."""
        from openhands.sdk.skills import install_skills_from_marketplace

        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "missing", "source": "./does-not-exist"}],
        )

        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        # Should not raise, just skip
        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir
        )

        assert len(installed) == 0

    def test_install_skills_from_plugin_directories(self, tmp_path: Path) -> None:
        """Test that skills inside plugin directories are also installed."""
        from openhands.sdk.skills import install_skills_from_marketplace

        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[],  # No standalone skills
            plugins=[{"name": "my-plugin", "source": "./plugins/my-plugin"}],
        )

        # Create plugin with skills inside
        plugin_dir = marketplace_dir / "plugins" / "my-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text('{"name": "my-plugin"}')

        skill_dir = plugin_dir / "skills" / "plugin-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plugin-skill\ndescription: From plugin\n---\n# plugin-skill"
        )

        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir
        )

        assert len(installed) == 1
        assert installed[0].name == "plugin-skill"

    def test_install_both_standalone_and_plugin_skills(self, tmp_path: Path) -> None:
        """Test installing skills from both standalone entries and plugins."""
        from openhands.sdk.skills import install_skills_from_marketplace

        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "standalone", "source": "./skills/standalone"}],
            plugins=[{"name": "my-plugin", "source": "./plugins/my-plugin"}],
        )

        # Create standalone skill
        standalone_dir = marketplace_dir / "skills" / "standalone"
        standalone_dir.mkdir(parents=True)
        (standalone_dir / "SKILL.md").write_text(
            "---\nname: standalone\ndescription: Standalone\n---\n# standalone"
        )

        # Create plugin with skill
        plugin_dir = marketplace_dir / "plugins" / "my-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text('{"name": "my-plugin"}')

        plugin_skill_dir = plugin_dir / "skills" / "from-plugin"
        plugin_skill_dir.mkdir(parents=True)
        (plugin_skill_dir / "SKILL.md").write_text(
            "---\nname: from-plugin\ndescription: From plugin\n---\n# from-plugin"
        )

        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir
        )

        names = {s.name for s in installed}
        assert names == {"standalone", "from-plugin"}
