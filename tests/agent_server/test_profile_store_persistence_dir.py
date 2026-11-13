"""Regression tests for credential-bearing persistence directories."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import PersistedSettings, ServerStores


@pytest.fixture
def isolated_persistence_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """``OH_PERSISTENCE_DIR`` pointed at a clean directory."""
    persistence_dir = tmp_path / "persistence"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(persistence_dir))
    return persistence_dir


def test_llm_profile_store_uses_persistence_dir(isolated_persistence_dir: Path) -> None:
    store = ServerStores.from_config(Config()).llm_profiles
    assert store.base_dir == isolated_persistence_dir / "profiles"
    assert store.list() == []


def test_agent_profile_store_uses_persistence_dir(
    isolated_persistence_dir: Path,
) -> None:
    store = ServerStores.from_config(Config()).agent_profiles
    assert store.base_dir == isolated_persistence_dir / "agent-profiles"
    assert store.list() == []


@pytest.fixture
def home_without_persistence_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """No ``OH_PERSISTENCE_DIR``; ``Path.home()`` redirected to a tempdir."""
    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def test_llm_profile_store_falls_back_to_home(
    home_without_persistence_env: Path,
) -> None:
    store = ServerStores.from_config(Config()).llm_profiles
    assert store.base_dir == home_without_persistence_env / ".openhands" / "profiles"


def test_agent_profile_store_falls_back_to_home(
    home_without_persistence_env: Path,
) -> None:
    store = ServerStores.from_config(Config()).agent_profiles
    assert (
        store.base_dir == home_without_persistence_env / ".openhands" / "agent-profiles"
    )


def test_settings_and_secrets_stores_fall_back_to_home(
    home_without_persistence_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    config = Config(conversations_path=Path("workspace/conversations"))
    stores = ServerStores.from_config(config)

    settings_store = stores.settings
    settings_store.save(PersistedSettings())
    secrets_store = stores.secrets
    secrets_store.set_secret("OPENAI_API_KEY", "sk-test")

    expected_dir = home_without_persistence_env / ".openhands"
    assert settings_store.persistence_dir == expected_dir
    assert secrets_store.persistence_dir == expected_dir
    assert (expected_dir / "settings.json").is_file()
    assert (expected_dir / "secrets.json").is_file()
    assert not (repo / "workspace" / ".openhands" / "settings.json").exists()
    assert not (repo / "workspace" / ".openhands" / "secrets.json").exists()


def test_profile_stores_do_not_read_home_directory(
    isolated_persistence_dir: Path,
) -> None:
    """The host user's ``~/.openhands/profiles/*.json`` must not appear."""
    stores = ServerStores.from_config(Config())
    llm = stores.llm_profiles
    agent = stores.agent_profiles

    # Both stores must materialize their own directory *inside* the
    # persistence dir, not anywhere else.
    assert llm.base_dir.is_dir()
    assert agent.base_dir.is_dir()
    home_profiles = Path.home() / ".openhands" / "profiles"
    assert llm.base_dir != home_profiles
    assert agent.base_dir != Path.home() / ".openhands" / "agent-profiles"

    # And the new dir should contain nothing the host happens to have.
    visible_names = set(llm.list()) | set(agent.list())
    assert visible_names == set()


def test_stores_keep_the_directory_and_cipher_they_were_built_with(
    isolated_persistence_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One server (re)initialisation, one location and key for all its stores.

    The environment is read when the stores are built. Changing it afterwards
    moves the stores of the next build, never half of the current ones.
    """
    config = Config(secret_key=SecretStr("first-key"))
    stores = ServerStores.from_config(config)
    settings_before = stores.settings

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "elsewhere"))

    assert stores.settings is settings_before
    assert stores.secrets.persistence_dir == isolated_persistence_dir
    assert stores.llm_profiles.base_dir == isolated_persistence_dir / "profiles"
    assert stores.secrets.cipher is stores.settings.cipher is config.cipher

    rebuilt = ServerStores.from_config(Config(secret_key=SecretStr("second-key")))
    assert rebuilt.settings.persistence_dir == tmp_path / "elsewhere"
    assert rebuilt.settings.cipher is not config.cipher


def test_building_stores_touches_no_file(isolated_persistence_dir: Path) -> None:
    """A dormant server builds its stores before its volume may be writable."""
    stores = ServerStores.from_config(Config())

    assert not isolated_persistence_dir.exists()

    stores.llm_profiles
    assert (isolated_persistence_dir / "profiles").is_dir()
