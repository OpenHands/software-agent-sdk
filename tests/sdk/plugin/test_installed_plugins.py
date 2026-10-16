"""Tests for installed plugins management.

These tests verify the public API in ``openhands.sdk.plugin.installed``
delegates correctly to ``InstallationManager``.  Internal metadata and
sync logic is already covered by ``tests/sdk/extensions/installation/``.

Integration tests (marked with @pytest.mark.network) test real GitHub
cloning and remain unchanged.
"""

import json
import subprocess
from pathlib import Path

import pytest

from openhands.sdk.plugin import (
    Plugin,
    PluginFetchError,
    disable_plugin,
    enable_plugin,
    get_installed_plugin,
    get_installed_plugins_dir,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
    update_plugin,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def installed_dir(tmp_path: Path) -> Path:
    installed = tmp_path / "installed"
    installed.mkdir(parents=True)
    return installed


@pytest.fixture
def sample_plugin_dir(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "sample-plugin"
    plugin_dir.mkdir(parents=True)

    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir()
    manifest = {
        "name": "sample-plugin",
        "version": "1.0.0",
        "description": "A sample plugin for testing",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))

    skills_dir = plugin_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n"
        "triggers:\n  - test\n---\n# Test Skill\n"
    )

    return plugin_dir


# ============================================================================
# Public API smoke tests
# ============================================================================


def test_get_installed_plugins_dir_returns_default_path():
    path = get_installed_plugins_dir()
    assert ".openhands" in str(path)
    assert "plugins" in str(path)
    assert "installed" in str(path)


def test_install_from_local_path(sample_plugin_dir: Path, installed_dir: Path) -> None:
    info = install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)

    assert info.name == "sample-plugin"
    assert info.version == "1.0.0"
    assert info.source == str(sample_plugin_dir)
    assert (installed_dir / "sample-plugin" / ".plugin" / "plugin.json").exists()


def test_install_already_exists_raises_error(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    with pytest.raises(FileExistsError, match="already installed"):
        install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)


def test_install_with_force_overwrites(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    marker = installed_dir / "sample-plugin" / "marker.txt"
    marker.write_text("original")

    install_plugin(
        source=str(sample_plugin_dir),
        installed_dir=installed_dir,
        force=True,
    )
    assert not marker.exists()


def test_uninstall_existing_plugin(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    assert uninstall_plugin("sample-plugin", installed_dir=installed_dir)
    assert not (installed_dir / "sample-plugin").exists()


def test_list_installed_plugins(sample_plugin_dir: Path, installed_dir: Path) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    plugins = list_installed_plugins(installed_dir=installed_dir)
    assert len(plugins) == 1
    assert plugins[0].name == "sample-plugin"


def test_load_installed_plugins(sample_plugin_dir: Path, installed_dir: Path) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    plugins = load_installed_plugins(installed_dir=installed_dir)
    assert len(plugins) == 1
    assert isinstance(plugins[0], Plugin)
    assert plugins[0].name == "sample-plugin"
    assert len(plugins[0].skills) == 1


def test_disable_plugin_filters_load(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    assert disable_plugin("sample-plugin", installed_dir=installed_dir)

    assert load_installed_plugins(installed_dir=installed_dir) == []
    info = get_installed_plugin("sample-plugin", installed_dir=installed_dir)
    assert info is not None
    assert info.enabled is False


def test_enable_plugin_restores_load(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    disable_plugin("sample-plugin", installed_dir=installed_dir)
    assert enable_plugin("sample-plugin", installed_dir=installed_dir)

    plugins = load_installed_plugins(installed_dir=installed_dir)
    assert len(plugins) == 1
    assert plugins[0].name == "sample-plugin"


def test_get_existing_plugin(sample_plugin_dir: Path, installed_dir: Path) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    info = get_installed_plugin("sample-plugin", installed_dir=installed_dir)
    assert info is not None
    assert info.name == "sample-plugin"


def test_get_nonexistent_plugin(installed_dir: Path) -> None:
    assert get_installed_plugin("nonexistent", installed_dir=installed_dir) is None


def test_update_existing_plugin_local(
    sample_plugin_dir: Path, installed_dir: Path
) -> None:
    install_plugin(source=str(sample_plugin_dir), installed_dir=installed_dir)
    disable_plugin("sample-plugin", installed_dir=installed_dir)

    (sample_plugin_dir / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sample-plugin",
                "version": "1.0.1",
                "description": "Updated plugin",
            }
        )
    )

    updated = update_plugin("sample-plugin", installed_dir=installed_dir)
    assert updated is not None
    assert updated.version == "1.0.1"
    assert updated.enabled is False


def test_update_nonexistent_plugin(installed_dir: Path) -> None:
    assert update_plugin("nonexistent", installed_dir=installed_dir) is None


def test_install_git_repo_root_without_manifest_is_named_after_repo(
    tmp_path: Path, installed_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest-less repository is named after the repository, not after
    its ``<repo>-<hash>`` clone cache directory."""
    monkeypatch.setattr(
        "openhands.sdk.extensions.installation.manager.DEFAULT_CACHE_DIR",
        tmp_path / "cache",
    )
    repo = tmp_path / "src" / "qa-src"
    skill_dir = repo / "skills" / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Say hello\n---\nhello\n"
    )
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)

    info = install_plugin(source=f"file://{repo}", installed_dir=installed_dir)

    assert info.name == "qa-src"
    assert info.install_path == installed_dir / "qa-src"
    assert (installed_dir / "qa-src" / "skills" / "hello" / "SKILL.md").exists()
    plugins = load_installed_plugins(installed_dir=installed_dir)
    assert [p.name for p in plugins] == ["qa-src"]


def test_update_git_repo_root_keeps_installed_name(
    tmp_path: Path, installed_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository root installed under another name (the cache directory
    name, before the fix) keeps that name when it is updated."""
    monkeypatch.setattr(
        "openhands.sdk.extensions.installation.manager.DEFAULT_CACHE_DIR",
        tmp_path / "cache",
    )
    repo = tmp_path / "src" / "qa-src"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("v1\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    install_plugin(source=f"file://{repo}", installed_dir=installed_dir)
    legacy = installed_dir / "qa-src-0123456789abcdef"
    (installed_dir / "qa-src").rename(legacy)
    metadata = installed_dir / ".installed.json"
    metadata.write_text(metadata.read_text().replace('"qa-src"', f'"{legacy.name}"'))

    updated = update_plugin(legacy.name, installed_dir=installed_dir)

    assert updated is not None
    assert updated.name == legacy.name
    assert [p.name for p in list_installed_plugins(installed_dir=installed_dir)] == [
        legacy.name
    ]


# ============================================================================
# Integration Tests (Real GitHub)
# ============================================================================


@pytest.mark.network
def test_install_from_github_with_repo_path(installed_dir: Path) -> None:
    try:
        info = install_plugin(
            source="github:OpenHands/agent-sdk",
            repo_path=(
                "examples/05_skills_and_plugins/"
                "02_loading_plugins/example_plugins/code-quality"
            ),
            installed_dir=installed_dir,
        )

        assert info.name == "code-quality"
        assert info.source == "github:OpenHands/agent-sdk"
        assert info.resolved_ref is not None
        assert info.repo_path is not None

        plugins = load_installed_plugins(installed_dir=installed_dir)
        code_quality = next((p for p in plugins if p.name == "code-quality"), None)
        assert code_quality is not None
        assert len(code_quality.get_all_skills()) >= 1

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")


@pytest.mark.network
def test_install_from_github_with_ref(installed_dir: Path) -> None:
    try:
        info = install_plugin(
            source="github:OpenHands/agent-sdk",
            ref="main",
            repo_path=(
                "examples/05_skills_and_plugins/"
                "02_loading_plugins/example_plugins/code-quality"
            ),
            installed_dir=installed_dir,
        )

        assert info.name == "code-quality"
        assert info.resolved_ref is not None
        assert len(info.resolved_ref) == 40

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")


@pytest.mark.network
def test_install_document_skills_plugin(installed_dir: Path) -> None:
    try:
        source = "github:anthropics/skills"
        info = install_plugin(
            source=source,
            ref="main",
            installed_dir=installed_dir,
        )

        expected_name = "skills"
        assert info.name == expected_name
        assert info.source == source

        install_path = info.install_path
        skills_dir = install_path / "skills"
        assert skills_dir.is_dir()

        for skill_name in ["pptx", "xlsx", "docx", "pdf"]:
            assert (skills_dir / skill_name).is_dir()
            assert (skills_dir / skill_name / "SKILL.md").exists()

        plugins = load_installed_plugins(installed_dir=installed_dir)
        doc_plugin = next((p for p in plugins if p.name == expected_name), None)
        assert doc_plugin is not None
        skills = doc_plugin.get_all_skills()
        assert len(skills) >= 4
        skill_names = {s.name for s in skills}
        assert {"pptx", "xlsx", "docx", "pdf"} <= skill_names

    except PluginFetchError:
        pytest.skip("GitHub not accessible (network issue)")
