"""Tests for PersistedSettings schema-version migrations and the startup
migration helper.

The #5205 orphaned-ACP data repair intentionally lives in the *startup*
helper, not in the payload-only v3 -> v4 migrator, because a safe
predicate needs cross-store awareness (the agent profile store). See
``_looks_like_orphaned_acp_from_deleted_profile`` for the predicate.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from openhands.agent_server.persistence import (
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    FileSettingsStore,
    PersistedSettings,
    apply_startup_settings_migrations,
)
from openhands.agent_server.persistence.store import (
    _looks_like_orphaned_acp_from_deleted_profile,
)


def _orphaned_acp_payload(
    *, active_agent_profile_id: str | None = None
) -> dict[str, Any]:
    """Build the on-disk shape produced by the pre-#5206 delete-profile bug."""
    return {
        "schema_version": 3,
        "agent_settings": {
            "schema_version": 6,
            "agent_kind": "acp",
            "acp_server": "codex",
            "acp_model": "gpt-5.5",
            "llm": {"model": "openhands/claude-sonnet-4-6"},
        },
        "conversation_settings": {"schema_version": 1, "max_iterations": 500},
        "active_profile": "claude-sonnet-4-6",
        "active_agent_profile_id": active_agent_profile_id,
        "misc_settings": {},
    }


def _openhands_summaries(name: str = "default") -> list[dict[str, Any]]:
    return [
        {
            "id": "profile-uuid",
            "name": name,
            "agent_kind": "openhands",
            "revision": 1,
            "llm_profile_ref": "some-llm-profile",
            "mcp_server_refs": [],
        }
    ]


@pytest.fixture
def persistence_dir() -> Path:
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


# ---------------------------------------------------------------------------
# Read-time migrator (v3 -> v4): version-only, no data changes
# ---------------------------------------------------------------------------


def test_v3_v4_migrator_bumps_version_without_touching_settings():
    """The payload-only migrator must not repair ACP data — cross-store
    context is needed to distinguish orphaned vs. legitimate ACP configs.
    """
    loaded = PersistedSettings.from_persisted(_orphaned_acp_payload())

    assert loaded.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    # Data is preserved by the read-time migrator; the startup helper repairs it.
    assert loaded.agent_settings.agent_kind == "acp"


def test_openhands_settings_pass_through_migrator_untouched():
    payload = _orphaned_acp_payload()
    payload["agent_settings"] = {
        "schema_version": 6,
        "agent_kind": "openhands",
        "llm": {"model": "openhands/claude-sonnet-4-6"},
    }

    loaded = PersistedSettings.from_persisted(payload)

    assert loaded.agent_settings.agent_kind == "openhands"
    assert loaded.agent_settings.llm.model == "openhands/claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Cross-store repair predicate
# ---------------------------------------------------------------------------


def test_predicate_matches_bug_fingerprint():
    settings = PersistedSettings.from_persisted(_orphaned_acp_payload())

    assert _looks_like_orphaned_acp_from_deleted_profile(
        settings, _openhands_summaries()
    )


def test_predicate_skips_direct_patch_acp_without_profiles():
    """Fresh install: user PATCH'd ACP directly, no profile store entries yet.

    Repairing here would silently discard the user's ACP configuration.
    """
    settings = PersistedSettings.from_persisted(_orphaned_acp_payload())

    assert not _looks_like_orphaned_acp_from_deleted_profile(
        settings, agent_profile_summaries=[]
    )


def test_predicate_skips_when_acp_profile_still_present():
    """If any ACP profile exists in the store the ACP settings are plausibly
    aligned with a live user choice; do not rewrite.
    """
    settings = PersistedSettings.from_persisted(_orphaned_acp_payload())
    summaries = [
        {
            "id": "acp-uuid",
            "name": "codex",
            "agent_kind": "acp",
            "revision": 1,
            "llm_profile_ref": None,
            "mcp_server_refs": [],
        }
    ]

    assert not _looks_like_orphaned_acp_from_deleted_profile(settings, summaries)


def test_predicate_skips_when_active_pointer_is_set():
    settings = PersistedSettings.from_persisted(
        _orphaned_acp_payload(active_agent_profile_id="live-profile-id")
    )

    assert not _looks_like_orphaned_acp_from_deleted_profile(
        settings, _openhands_summaries()
    )


def test_predicate_skips_openhands_settings():
    payload = _orphaned_acp_payload()
    payload["agent_settings"] = {
        "schema_version": 6,
        "agent_kind": "openhands",
        "llm": {"model": "openhands/claude-sonnet-4-6"},
    }
    settings = PersistedSettings.from_persisted(payload)

    assert not _looks_like_orphaned_acp_from_deleted_profile(
        settings, _openhands_summaries()
    )


# ---------------------------------------------------------------------------
# Startup persistence helper
# ---------------------------------------------------------------------------


def test_startup_repair_lands_on_disk(persistence_dir: Path):
    settings_path = persistence_dir / "settings.json"
    settings_path.write_text(json.dumps(_orphaned_acp_payload()))
    settings_path.chmod(0o600)

    store = FileSettingsStore(persistence_dir=persistence_dir)

    assert (
        apply_startup_settings_migrations(
            store, agent_profile_summaries=_openhands_summaries()
        )
        is True
    )

    on_disk = json.loads(settings_path.read_text())
    assert on_disk["schema_version"] == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert on_disk["agent_settings"]["agent_kind"] == "openhands"
    # Stale ACP fields are gone (default OpenHands settings do not carry them).
    assert "acp_server" not in on_disk["agent_settings"]
    assert "acp_model" not in on_disk["agent_settings"]


def test_startup_migration_preserves_direct_patch_acp(persistence_dir: Path):
    """Cross-store predicate: an ACP config with an empty profile store
    is left as-is. Only the schema_version is advanced.
    """
    settings_path = persistence_dir / "settings.json"
    settings_path.write_text(json.dumps(_orphaned_acp_payload()))
    settings_path.chmod(0o600)

    store = FileSettingsStore(persistence_dir=persistence_dir)

    assert (
        apply_startup_settings_migrations(store, agent_profile_summaries=[])
        is True  # schema bump only
    )

    on_disk = json.loads(settings_path.read_text())
    assert on_disk["schema_version"] == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert on_disk["agent_settings"]["agent_kind"] == "acp"
    assert on_disk["agent_settings"]["acp_server"] == "codex"


def test_startup_migration_is_noop_when_no_file(persistence_dir: Path):
    store = FileSettingsStore(persistence_dir=persistence_dir)

    assert apply_startup_settings_migrations(store, agent_profile_summaries=[]) is False
    assert not (persistence_dir / "settings.json").exists()


def test_startup_migration_is_noop_at_current_version_and_healthy(
    persistence_dir: Path,
):
    settings_path = persistence_dir / "settings.json"
    current_payload = {
        "schema_version": PERSISTED_SETTINGS_SCHEMA_VERSION,
        "agent_settings": {
            "schema_version": 6,
            "agent_kind": "openhands",
            "llm": {"model": "openhands/claude-sonnet-4-6"},
        },
        "conversation_settings": {"schema_version": 1},
        "misc_settings": {},
    }
    settings_path.write_text(json.dumps(current_payload))
    settings_path.chmod(0o600)
    mtime_before = settings_path.stat().st_mtime_ns

    store = FileSettingsStore(persistence_dir=persistence_dir)

    assert (
        apply_startup_settings_migrations(
            store, agent_profile_summaries=_openhands_summaries()
        )
        is False
    )
    assert settings_path.stat().st_mtime_ns == mtime_before


def test_startup_migration_repairs_at_current_version_when_predicate_matches(
    persistence_dir: Path,
):
    """A file already at v4 can still match the repair predicate if it was
    written before the startup step landed. Predicate must still fire.
    """
    settings_path = persistence_dir / "settings.json"
    payload = _orphaned_acp_payload()
    payload["schema_version"] = PERSISTED_SETTINGS_SCHEMA_VERSION
    settings_path.write_text(json.dumps(payload))
    settings_path.chmod(0o600)

    store = FileSettingsStore(persistence_dir=persistence_dir)

    assert (
        apply_startup_settings_migrations(
            store, agent_profile_summaries=_openhands_summaries()
        )
        is True
    )
    on_disk = json.loads(settings_path.read_text())
    assert on_disk["agent_settings"]["agent_kind"] == "openhands"


def test_startup_migration_is_noop_on_corrupted_file(persistence_dir: Path):
    """Corrupted files must not be rewritten (data-loss protection)."""
    settings_path = persistence_dir / "settings.json"
    settings_path.write_text("{ not json")
    settings_path.chmod(0o600)
    original = settings_path.read_bytes()

    store = FileSettingsStore(persistence_dir=persistence_dir)

    assert apply_startup_settings_migrations(store, agent_profile_summaries=[]) is False
    assert settings_path.read_bytes() == original


def test_startup_migration_second_call_is_noop(persistence_dir: Path):
    """Once a boot writes the fix, subsequent boots don't touch the file again."""
    settings_path = persistence_dir / "settings.json"
    settings_path.write_text(json.dumps(_orphaned_acp_payload()))
    settings_path.chmod(0o600)

    store = FileSettingsStore(persistence_dir=persistence_dir)
    summaries = _openhands_summaries()
    assert (
        apply_startup_settings_migrations(store, agent_profile_summaries=summaries)
        is True
    )

    mtime_after_first = settings_path.stat().st_mtime_ns
    assert (
        apply_startup_settings_migrations(store, agent_profile_summaries=summaries)
        is False
    )
    assert settings_path.stat().st_mtime_ns == mtime_after_first
