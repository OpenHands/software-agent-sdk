"""Regression test for #3815.

Without this, ``get_llm_profile_store`` / ``get_agent_profile_store`` would
read and write the user's ``~/.openhands/profiles/`` and
``~/.openhands/agent-profiles/`` regardless of ``OH_PERSISTENCE_DIR``,
leaking host state into supposedly-isolated agent-server instances and
making first-run / onboarding tests non-reproducible.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_llm_profile_store,
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
