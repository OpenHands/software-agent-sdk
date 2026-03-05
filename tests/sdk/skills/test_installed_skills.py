"""Tests for installed skills management."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.skills import (
    InstalledSkillsMetadata,
    disable_skill,
    enable_skill,
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


def test_disable_skill_filters_load(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)

    assert disable_skill("sample-skill", installed_dir=installed_dir) is True

    skills = load_installed_skills(installed_dir=installed_dir)
    assert skills == []
    info = get_installed_skill("sample-skill", installed_dir=installed_dir)
    assert info is not None
    assert info.enabled is False


def test_enable_skill_restores_load(
    sample_skill_dir: Path, installed_dir: Path
) -> None:
    install_skill(source=str(sample_skill_dir), installed_dir=installed_dir)
    disable_skill("sample-skill", installed_dir=installed_dir)

    assert enable_skill("sample-skill", installed_dir=installed_dir) is True

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
    disable_skill("sample-skill", installed_dir=installed_dir)

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
    assert info.enabled is False
    installed_content = (installed_dir / "sample-skill" / "SKILL.md").read_text()
    assert "Updated description" in installed_content


def test_metadata_invalid_json_returns_empty(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    installed.mkdir()
    metadata_path = installed / ".installed.json"
    metadata_path.write_text("invalid json {")

    metadata = InstalledSkillsMetadata.load_from_dir(installed)

    assert metadata.skills == {}
