"""Tests for installed skills management.

These tests verify the public API in ``openhands.sdk.skills.installed``
delegates correctly to ``InstallationManager``.  Internal metadata and
sync logic is already covered by ``tests/sdk/extensions/installation/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openhands.sdk.skills import (
    Skill,
    disable_skill,
    enable_skill,
    get_installed_skill,
    get_installed_skills_dir,
    install_skill,
    install_skills_from_marketplace,
    list_installed_skills,
    load_installed_skills,
    uninstall_skill,
    update_skill,
)


def _create_skill_dir(
    base_dir: Path,
    dir_name: str,
    *,
    description: str = "A test skill",
) -> Path:
    skill_dir = base_dir / dir_name
    skill_dir.mkdir(parents=True)
    skill_md = f"---\nname: {dir_name}\ndescription: {description}\n---\n# {dir_name}\n"
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


# ============================================================================
# Public API smoke tests
# ============================================================================


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
    assert (installed_dir / "sample-skill" / "SKILL.md").exists()


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
    marker = installed_dir / "sample-skill" / "marker.txt"
    marker.write_text("original")

    install_skill(
        source=str(sample_skill_dir),
        installed_dir=installed_dir,
        force=True,
    )
    assert not marker.exists()


def test_uninstall_existing_skill(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    assert uninstall_skill("sample-skill", installed_dir=installed_dir)
    assert not (installed_dir / "sample-skill").exists()


def test_list_installed_skills(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    skills = list_installed_skills(installed_dir=installed_dir)
    assert len(skills) == 1
    assert skills[0].name == "sample-skill"


def test_load_installed_skills(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    skills = load_installed_skills(installed_dir=installed_dir)
    assert len(skills) == 1
    assert isinstance(skills[0], Skill)
    assert skills[0].name == "sample-skill"


def test_disable_skill_filters_load(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    assert disable_skill("sample-skill", installed_dir=installed_dir)

    assert load_installed_skills(installed_dir=installed_dir) == []
    info = get_installed_skill("sample-skill", installed_dir=installed_dir)
    assert info is not None
    assert info.enabled is False


def test_enable_skill_restores_load(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    disable_skill("sample-skill", installed_dir=installed_dir)
    assert enable_skill("sample-skill", installed_dir=installed_dir)

    skills = load_installed_skills(installed_dir=installed_dir)
    assert len(skills) == 1
    assert skills[0].name == "sample-skill"


def test_get_installed_skill(sample_skill_dir: Path, installed_dir: Path) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    info = get_installed_skill("sample-skill", installed_dir=installed_dir)
    assert info is not None
    assert info.name == "sample-skill"


def test_get_nonexistent_skill(installed_dir: Path) -> None:
    assert get_installed_skill("nonexistent", installed_dir=installed_dir) is None


def test_update_skill_reinstalls_from_source(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    disable_skill("sample-skill", installed_dir=installed_dir)

    (sample_skill_dir / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: Updated description\n"
        "---\n# sample-skill\n"
    )

    info = update_skill("sample-skill", installed_dir=installed_dir)
    assert info is not None
    assert info.description == "Updated description"
    assert info.enabled is False
    content = (installed_dir / "sample-skill" / "SKILL.md").read_text()
    assert "Updated description" in content


def test_update_nonexistent_skill(installed_dir: Path) -> None:
    assert update_skill("nonexistent", installed_dir=installed_dir) is None


# ============================================================================
# Marketplace tests
# ============================================================================


def _create_marketplace(
    base_dir: Path,
    skills: list[dict[str, str]],
    plugins: list[dict[str, str]] | None = None,
) -> Path:
    marketplace_dir = base_dir / "marketplace"
    marketplace_dir.mkdir(parents=True)
    plugin_dir = marketplace_dir / ".plugin"
    plugin_dir.mkdir()
    manifest = {
        "name": "test-marketplace",
        "owner": {"name": "Test"},
        "skills": skills,
        "plugins": plugins or [],
    }
    (plugin_dir / "marketplace.json").write_text(json.dumps(manifest))
    return marketplace_dir


class TestInstallSkillsFromMarketplace:
    def test_install_local_skills(self, tmp_path: Path) -> None:
        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "my-skill", "source": "./skills/my-skill"}],
        )
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

    def test_install_skills_force_overwrite(self, tmp_path: Path) -> None:
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

        install_skills_from_marketplace(marketplace_dir, installed_dir=installed_dir)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Updated\n---\n# my-skill"
        )

        # Without force — already exists
        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir, force=False
        )
        assert len(installed) == 0

        # With force — overwrites
        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir, force=True
        )
        assert len(installed) == 1
        content = (installed_dir / "my-skill" / "SKILL.md").read_text()
        assert "Updated" in content

    def test_install_handles_missing_skill_source(self, tmp_path: Path) -> None:
        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "missing", "source": "./does-not-exist"}],
        )
        installed_dir = tmp_path / "installed"
        installed_dir.mkdir()

        installed = install_skills_from_marketplace(
            marketplace_dir, installed_dir=installed_dir
        )
        assert len(installed) == 0

    def test_install_skills_from_plugin_directories(self, tmp_path: Path) -> None:
        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[],
            plugins=[{"name": "my-plugin", "source": "./plugins/my-plugin"}],
        )
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
        marketplace_dir = _create_marketplace(
            tmp_path,
            skills=[{"name": "standalone", "source": "./skills/standalone"}],
            plugins=[{"name": "my-plugin", "source": "./plugins/my-plugin"}],
        )
        standalone_dir = marketplace_dir / "skills" / "standalone"
        standalone_dir.mkdir(parents=True)
        (standalone_dir / "SKILL.md").write_text(
            "---\nname: standalone\ndescription: Standalone\n---\n# standalone"
        )

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
