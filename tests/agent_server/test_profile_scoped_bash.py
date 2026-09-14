"""Profile-selected secrets for non-conversation bash commands."""

from pathlib import Path

import pytest

from openhands.agent_server.persistence import (
    FileSecretsStore,
    get_agent_profile_store,
    reset_stores,
)
from openhands.agent_server.profile_secrets import command_secret_registry
from openhands.sdk.profiles.agent_profile import OpenHandsAgentProfile


@pytest.fixture
def profile_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    reset_stores()
    try:
        yield FileSecretsStore(tmp_path)
    finally:
        reset_stores()


def test_command_registry_contains_only_selected_saved_secrets(profile_stores):
    profile_stores.set_secret("SELECTED_TOKEN", "selected-value")
    profile_stores.set_secret("UNSELECTED_TOKEN", "unselected-value")
    profile = OpenHandsAgentProfile(
        name="automation-scanner",
        llm_profile_ref="default",
        secret_refs=["SELECTED_TOKEN"],
    )
    get_agent_profile_store().save(profile)

    registry = command_secret_registry(profile.id, profile_stores)

    assert registry.get_all_secrets_as_env_vars() == {
        "SELECTED_TOKEN": "selected-value"
    }


def test_command_registry_rejects_missing_selected_secret(profile_stores):
    profile = OpenHandsAgentProfile(
        name="automation-scanner",
        llm_profile_ref="default",
        secret_refs=["MISSING_TOKEN"],
    )
    get_agent_profile_store().save(profile)

    with pytest.raises(ValueError, match="MISSING_TOKEN"):
        command_secret_registry(profile.id, profile_stores)


def test_command_registry_rejects_unknown_profile(profile_stores):
    from uuid import uuid4

    with pytest.raises(ValueError, match="not found"):
        command_secret_registry(uuid4(), profile_stores)
