import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest

from openhands.agent_server import marketplace_snapshot, plugins_service, skills_service


@pytest.fixture(autouse=True)
def _reset_marketplace_cache() -> Iterator[None]:
    marketplace_snapshot._marketplace_cache.clear()
    yield
    marketplace_snapshot._marketplace_cache.clear()


def _write_marketplace(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": name,
                "owner": {"name": "Test"},
                "plugins": [
                    {"name": "plugin", "source": "./plugins/plugin"},
                    {"name": "skill-plugin", "source": "./skills/skill-plugin"},
                ],
                "skills": [
                    {"name": "plugin", "source": "./skills/plugin"},
                    {"name": "standalone", "source": "./skills/standalone"},
                ],
            }
        )
    )


def test_manifest_discovery_precedes_explicit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "extensions"
    _write_marketplace(repo / ".plugin" / "marketplace.json", "manifest")
    _write_marketplace(repo / "marketplaces" / "default.json", "fallback")
    monkeypatch.setattr(
        marketplace_snapshot, "update_skills_repository", lambda *args: repo
    )

    marketplace = marketplace_snapshot.load_marketplace_snapshot()

    assert marketplace is not None
    assert marketplace.name == "manifest"


def test_explicit_path_is_used_as_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "extensions"
    _write_marketplace(repo / "marketplaces" / "custom.json", "fallback")
    monkeypatch.setattr(
        marketplace_snapshot, "update_skills_repository", lambda *args: repo
    )

    marketplace = marketplace_snapshot.load_marketplace_snapshot(
        "marketplaces/custom.json"
    )

    assert marketplace is not None
    assert marketplace.name == "fallback"


def test_only_successful_snapshots_are_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "extensions"
    _write_marketplace(repo / ".plugin" / "marketplace.json", "manifest")
    update = Mock(side_effect=[None, repo])
    monkeypatch.setattr(marketplace_snapshot, "update_skills_repository", update)

    assert marketplace_snapshot.load_marketplace_snapshot() is None
    loaded = marketplace_snapshot.load_marketplace_snapshot()
    cached = marketplace_snapshot.load_marketplace_snapshot()

    assert loaded is cached
    assert update.call_count == 2


def test_parse_failure_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid_repo = tmp_path / "invalid"
    invalid_manifest = invalid_repo / ".plugin" / "marketplace.json"
    invalid_manifest.parent.mkdir(parents=True)
    invalid_manifest.write_text("not json")
    valid_repo = tmp_path / "valid"
    _write_marketplace(valid_repo / ".plugin" / "marketplace.json", "manifest")
    update = Mock(side_effect=[invalid_repo, valid_repo])
    monkeypatch.setattr(marketplace_snapshot, "update_skills_repository", update)

    assert marketplace_snapshot.load_marketplace_snapshot() is None
    assert marketplace_snapshot.load_marketplace_snapshot() is not None
    assert update.call_count == 2


def test_skills_and_plugins_share_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "extensions"
    _write_marketplace(repo / ".plugin" / "marketplace.json", "manifest")
    update = Mock(return_value=repo)
    monkeypatch.setattr(marketplace_snapshot, "update_skills_repository", update)
    monkeypatch.setattr(skills_service, "service_list_installed_skills", lambda **k: [])
    monkeypatch.setattr(plugins_service, "list_installed_plugins", lambda **k: [])

    skills = skills_service.service_get_marketplace_catalog()
    plugins = plugins_service.service_get_plugins_marketplace_catalog()

    assert [entry.name for entry in skills] == [
        "plugin",
        "skill-plugin",
        "standalone",
    ]
    assert [entry.name for entry in plugins] == ["plugin"]
    assert Path(skills[0].source).parts[-2:] == ("plugins", "plugin")
    update.assert_called_once()


def test_installed_state_is_computed_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "extensions"
    _write_marketplace(repo / ".plugin" / "marketplace.json", "manifest")
    monkeypatch.setattr(
        marketplace_snapshot, "update_skills_repository", lambda *args: repo
    )
    installed_skill = Mock()
    installed_skill.name = "skill-plugin"
    installed_plugin = Mock()
    installed_plugin.name = "plugin"
    skill_installs = Mock(side_effect=[[], [installed_skill]])
    plugin_installs = Mock(side_effect=[[], [installed_plugin]])
    monkeypatch.setattr(skills_service, "service_list_installed_skills", skill_installs)
    monkeypatch.setattr(plugins_service, "list_installed_plugins", plugin_installs)

    first_skills = skills_service.service_get_marketplace_catalog()
    second_skills = skills_service.service_get_marketplace_catalog()
    first_plugins = plugins_service.service_get_plugins_marketplace_catalog()
    second_plugins = plugins_service.service_get_plugins_marketplace_catalog()

    first_skill = next(item for item in first_skills if item.name == "skill-plugin")
    second_skill = next(item for item in second_skills if item.name == "skill-plugin")
    assert first_skill.installed is False
    assert second_skill.installed is True
    assert first_plugins[0].installed is False
    assert second_plugins[0].installed is True
