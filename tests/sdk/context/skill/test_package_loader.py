"""Tests for skill package loading functionality."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from openhands.sdk.context.skills.package_loader import (
    get_skill_package,
    list_skill_packages,
    load_configured_package_skills,
    load_packages_config,
    load_skills_from_package,
    load_skills_from_packages,
)


@pytest.fixture
def mock_entry_points():
    """Create mock entry points for testing."""
    # Create a mock entry point
    mock_ep = MagicMock()
    mock_ep.name = "test-package"

    # Create a mock module
    mock_module = MagicMock()
    mock_module.__name__ = "test_package_module"

    # Mock the manifest.json content (Claude Code format)
    manifest_content = """{
  "name": "test-package",
  "version": "1.0.0",
  "displayName": "Test Package",
  "description": "A test skill package",
  "author": "Test Author",
  "skills": [
    {
      "name": "test-skill",
      "path": "skills/test.md"
    }
  ]
}"""

    # Mock the skill file content
    skill_content = """---
triggers:
  - test
  - testing
---

# Test Skill

This is a test skill for testing package loading.
"""

    mock_ep.load.return_value = mock_module

    return mock_ep, mock_module, manifest_content, skill_content


def test_list_skill_packages_empty():
    """Test listing packages when none are installed."""
    with patch("openhands.sdk.context.skills.package_loader.entry_points") as mock_eps:
        mock_eps.return_value = []
        packages = list_skill_packages()
        assert packages == []


def test_list_skill_packages_with_package(mock_entry_points):
    """Test listing packages when one is installed."""
    mock_ep, mock_module, manifest_content, skill_content = mock_entry_points

    with patch("openhands.sdk.context.skills.package_loader.entry_points") as mock_eps:
        mock_eps.return_value = [mock_ep]

        with patch("openhands.sdk.context.skills.package_loader.files") as mock_files:
            mock_path = MagicMock()
            mock_path.read_text.return_value = manifest_content
            mock_files.return_value.joinpath.return_value = mock_path

            packages = list_skill_packages()

            assert len(packages) == 1
            assert packages[0]["name"] == "test-package"
            assert (
                packages[0]["descriptor"]["metadata"]["displayName"] == "Test Package"
            )


def test_get_skill_package_not_found():
    """Test getting a package that doesn't exist."""
    with patch("openhands.sdk.context.skills.package_loader.entry_points") as mock_eps:
        mock_eps.return_value = []
        package = get_skill_package("nonexistent")
        assert package is None


def test_get_skill_package_found(mock_entry_points):
    """Test getting an existing package."""
    mock_ep, mock_module, manifest_content, skill_content = mock_entry_points

    with patch("openhands.sdk.context.skills.package_loader.entry_points") as mock_eps:
        mock_eps.return_value = [mock_ep]

        with patch("openhands.sdk.context.skills.package_loader.files") as mock_files:
            mock_path = MagicMock()
            mock_path.read_text.return_value = manifest_content
            mock_files.return_value.joinpath.return_value = mock_path

            package = get_skill_package("test-package")

            assert package is not None
            assert package["name"] == "test-package"
            assert package["descriptor"]["metadata"]["displayName"] == "Test Package"


def test_load_skills_from_package_not_found():
    """Test loading skills from a non-existent package."""
    with patch("openhands.sdk.context.skills.package_loader.entry_points") as mock_eps:
        mock_eps.return_value = []

        with pytest.raises(ValueError, match="not found"):
            load_skills_from_package("nonexistent")


def test_load_skills_from_package_success(mock_entry_points):
    """Test successfully loading skills from a package."""
    mock_ep, mock_module, manifest_content, skill_content = mock_entry_points

    with patch("openhands.sdk.context.skills.package_loader.entry_points") as mock_eps:
        mock_eps.return_value = [mock_ep]

        with patch("openhands.sdk.context.skills.package_loader.files") as mock_files:
            # Mock reading both the manifest.json and skill file
            def joinpath_side_effect(path):
                mock_path = MagicMock()
                path_str = str(path)
                if "manifest.json" in path_str:
                    mock_path.read_text.return_value = manifest_content
                elif "skills/test.md" in path_str:
                    mock_path.read_text.return_value = skill_content
                else:
                    mock_path.read_text.return_value = ""
                return mock_path

            mock_files.return_value.joinpath.side_effect = joinpath_side_effect

            repo_skills, knowledge_skills = load_skills_from_package("test-package")

            # The test skill has triggers, so it should be a knowledge skill
            assert len(knowledge_skills) == 1
            assert "test-skill" in knowledge_skills
            assert knowledge_skills["test-skill"].name == "test-skill"
            assert knowledge_skills["test-skill"].trigger is not None


def test_load_skills_from_packages_multiple():
    """Test loading skills from multiple packages."""
    with patch(
        "openhands.sdk.context.skills.package_loader.load_skills_from_package"
    ) as mock_load:
        # Mock two different packages
        mock_load.side_effect = [
            ({"repo1": MagicMock()}, {"know1": MagicMock()}),
            ({"repo2": MagicMock()}, {"know2": MagicMock()}),
        ]

        repo_skills, knowledge_skills = load_skills_from_packages(
            ["package1", "package2"]
        )

        assert len(repo_skills) == 2
        assert len(knowledge_skills) == 2
        assert mock_load.call_count == 2


def test_load_packages_config_no_file():
    """Test loading config when no file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "nonexistent.yaml"
        packages = load_packages_config(config_path)
        assert packages == []


def test_load_packages_config_with_file():
    """Test loading config from an existing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "packages.yaml"
        config_content = {"packages": ["package1", "package2", "package3"]}

        config_path.write_text(yaml.dump(config_content))

        packages = load_packages_config(config_path)

        assert len(packages) == 3
        assert "package1" in packages
        assert "package2" in packages
        assert "package3" in packages


def test_load_packages_config_empty_packages():
    """Test loading config with empty packages list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "packages.yaml"
        config_content = {"packages": []}

        config_path.write_text(yaml.dump(config_content))

        packages = load_packages_config(config_path)

        assert packages == []


def test_load_packages_config_auto_discovery():
    """Test auto-discovery of config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create .openhands directory
        openhands_dir = Path(tmpdir) / ".openhands"
        openhands_dir.mkdir()

        config_path = openhands_dir / "packages.yaml"
        config_content = {"packages": ["auto-discovered-package"]}
        config_path.write_text(yaml.dump(config_content))

        # Change to temp directory
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            packages = load_packages_config()
            assert "auto-discovered-package" in packages
        finally:
            os.chdir(old_cwd)


def test_load_configured_package_skills():
    """Test loading skills from configured packages."""
    with patch(
        "openhands.sdk.context.skills.package_loader.load_packages_config"
    ) as mock_config:
        mock_config.return_value = ["package1", "package2"]

        with patch(
            "openhands.sdk.context.skills.package_loader.load_skills_from_packages"
        ) as mock_load:
            mock_load.return_value = ({"repo": MagicMock()}, {"know": MagicMock()})

            repo_skills, knowledge_skills = load_configured_package_skills()

            mock_config.assert_called_once()
            mock_load.assert_called_once_with(["package1", "package2"])
            assert len(repo_skills) == 1
            assert len(knowledge_skills) == 1


def test_load_configured_package_skills_no_config():
    """Test loading skills when no packages are configured."""
    with patch(
        "openhands.sdk.context.skills.package_loader.load_packages_config"
    ) as mock_config:
        mock_config.return_value = []

        repo_skills, knowledge_skills = load_configured_package_skills()

        assert repo_skills == {}
        assert knowledge_skills == {}
