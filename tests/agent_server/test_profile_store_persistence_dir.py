"""Regression tests for credential-bearing persistence directories."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_secrets_store,
    get_settings_store,
    reset_stores,
)


@pytest.fixture
def isolated_persistence_dir() -> Iterator[Path]:
    """``OH_PERSISTENCE_DIR`` pointed at a clean tempdir, stores reset."""
    with tempfile.TemporaryDirectory() as tmpdir:
        reset_stores()
        old_val = os.environ.get("OH_PERSISTENCE_DIR")
        os.environ["OH_PERSISTENCE_DIR"] = tmpdir
        try:
            yield Path(tmpdir)
        finally:
            reset_stores()
            if old_val is not None:
                os.environ["OH_PERSISTENCE_DIR"] = old_val
            else:
                os.environ.pop("OH_PERSISTENCE_DIR", None)


def test_llm_profile_store_uses_persistence_dir(isolated_persistence_dir: Path) -> None:
    store = get_llm_profile_store()
    assert store.base_dir == isolated_persistence_dir / "profiles"
    assert store.list() == []


def test_agent_profile_store_uses_persistence_dir(
    isolated_persistence_dir: Path,
) -> None:
    store = get_agent_profile_store()
    assert store.base_dir == isolated_persistence_dir / "agent-profiles"
    assert store.list() == []


@pytest.fixture
def home_without_persistence_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """No ``OH_PERSISTENCE_DIR``; ``Path.home()`` redirected to a tempdir."""
    reset_stores()
    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    try:
        yield fake_home
    finally:
        reset_stores()


def test_llm_profile_store_falls_back_to_home(
    home_without_persistence_env: Path,
) -> None:
    store = get_llm_profile_store()
    assert store.base_dir == home_without_persistence_env / ".openhands" / "profiles"


def test_agent_profile_store_falls_back_to_home(
    home_without_persistence_env: Path,
) -> None:
    store = get_agent_profile_store()
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

    settings_store = get_settings_store(config)
    settings_store.save(PersistedSettings())
    secrets_store = get_secrets_store(config)
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
    llm = get_llm_profile_store()
    agent = get_agent_profile_store()

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
