"""Tests for the LLM-profile <- AgentProfile foreign-key lifecycle."""

import concurrent.futures
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentProfileStore,
    OpenHandsAgentProfile,
    ProfileReferenced,
    cascade_rename,
    delete_llm_profile,
    find_referrers,
    rename_llm_profile,
)
from openhands.sdk.skills import Skill
from openhands.sdk.utils.cipher import Cipher


@pytest.fixture
def agent_store(tmp_path: Path) -> AgentProfileStore:
    return AgentProfileStore(base_dir=tmp_path / "agent-profiles")


@pytest.fixture
def llm_store(tmp_path: Path) -> LLMProfileStore:
    return LLMProfileStore(base_dir=tmp_path / "llm-profiles")


def _oh(name: str, llm_profile_ref: str) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(name=name, llm_profile_ref=llm_profile_ref)


def _ref(store: AgentProfileStore, name: str) -> str:
    """Load a profile and return its ``llm_profile_ref`` (narrows the union)."""
    loaded = store.load(name)
    assert isinstance(loaded, OpenHandsAgentProfile)
    return loaded.llm_profile_ref


# ── find_referrers ──────────────────────────────────────────────────────────


def test_find_referrers_empty(agent_store: AgentProfileStore) -> None:
    assert find_referrers(agent_store, "default") == []


def test_find_referrers_matches_only_citing_openhands(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(_oh("a", "default"))
    agent_store.save(_oh("b", "default"))
    agent_store.save(_oh("c", "other"))
    agent_store.save(ACPAgentProfile(name="d", acp_server="codex"))

    referrers = find_referrers(agent_store, "default")
    assert sorted(referrers) == ["a", "b"]


# ── cascade_rename ──────────────────────────────────────────────────────────


def test_cascade_rename_rewrites_matching_refs(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(_oh("a", "default"))
    agent_store.save(_oh("b", "default"))
    agent_store.save(_oh("c", "other"))

    rewritten = cascade_rename(agent_store, "default", "renamed")

    assert sorted(rewritten) == ["a", "b"]
    assert _ref(agent_store, "a") == "renamed"
    assert _ref(agent_store, "b") == "renamed"
    # Non-matching profile is untouched.
    assert _ref(agent_store, "c") == "other"
    assert find_referrers(agent_store, "default") == []


def test_cascade_rename_no_match_is_noop(agent_store: AgentProfileStore) -> None:
    agent_store.save(_oh("a", "other"))
    assert cascade_rename(agent_store, "default", "renamed") == []
    assert _ref(agent_store, "a") == "other"


def test_cascade_rename_preserves_id_and_encrypted_secret(
    agent_store: AgentProfileStore,
) -> None:
    cipher = Cipher(secret_key="test-key")
    secret = "ghp_DO_NOT_LEAK"
    profile = OpenHandsAgentProfile(
        name="a",
        llm_profile_ref="default",
        skills=[
            Skill(
                name="s",
                content="x",
                mcp_tools={
                    "svc": MCPServer(command="run", env={"K": SecretStr(secret)})
                },
            )
        ],
    )
    agent_store.save(profile, cipher=cipher)

    cascade_rename(agent_store, "default", "renamed")

    raw = (agent_store.base_dir / "a.json").read_text()
    assert secret not in raw  # encrypted token survives the surgical edit
    data = json.loads(raw)
    assert data["id"] == str(profile.id)
    assert data["llm_profile_ref"] == "renamed"
    token = data["skills"][0]["mcp_tools"]["svc"]["env"]["K"]
    decrypted = cipher.decrypt(token)
    assert decrypted is not None
    assert decrypted.get_secret_value() == secret


def test_cascade_rename_invalid_new_name_raises(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(_oh("a", "default"))
    with pytest.raises(ValueError, match="Invalid profile name"):
        cascade_rename(agent_store, "default", "../escape")


# ── ProfileReferenced ───────────────────────────────────────────────────────


def test_profile_referenced_message_names_referrers() -> None:
    exc = ProfileReferenced(["a", "b"])
    assert exc.referrers == ["a", "b"]
    assert "a" in str(exc)
    assert "b" in str(exc)


# ── delete_llm_profile (guarded) ────────────────────────────────────────────


def test_delete_llm_profile_blocked_when_referenced(
    agent_store: AgentProfileStore, llm_store: LLMProfileStore
) -> None:
    llm_store.save("default", LLM(usage_id="x", model="gpt-4-turbo"))
    agent_store.save(_oh("a", "default"))

    with pytest.raises(ProfileReferenced) as exc_info:
        delete_llm_profile(agent_store, llm_store, "default")

    assert exc_info.value.referrers == ["a"]
    # The LLM profile must NOT have been deleted.
    assert "default.json" in llm_store.list()


def test_delete_llm_profile_succeeds_when_unreferenced(
    agent_store: AgentProfileStore, llm_store: LLMProfileStore
) -> None:
    llm_store.save("default", LLM(usage_id="x", model="gpt-4-turbo"))
    agent_store.save(_oh("a", "other"))

    delete_llm_profile(agent_store, llm_store, "default")
    assert "default.json" not in llm_store.list()


# ── rename_llm_profile (guarded cascade) ────────────────────────────────────


def test_rename_llm_profile_renames_and_cascades(
    agent_store: AgentProfileStore, llm_store: LLMProfileStore
) -> None:
    llm_store.save("default", LLM(usage_id="x", model="gpt-4-turbo"))
    agent_store.save(_oh("a", "default"))
    agent_store.save(_oh("b", "default"))

    rewritten = rename_llm_profile(agent_store, llm_store, "default", "renamed")

    assert sorted(rewritten) == ["a", "b"]
    assert "renamed.json" in llm_store.list()
    assert "default.json" not in llm_store.list()
    assert _ref(agent_store, "a") == "renamed"


def test_rename_llm_profile_missing_source_leaves_refs_intact(
    agent_store: AgentProfileStore, llm_store: LLMProfileStore
) -> None:
    agent_store.save(_oh("a", "default"))

    with pytest.raises(FileNotFoundError):
        rename_llm_profile(agent_store, llm_store, "default", "renamed")

    # The LLM rename failed before any cascade, so refs are untouched.
    assert _ref(agent_store, "a") == "default"


# ── Concurrency ─────────────────────────────────────────────────────────────


def test_cascade_rename_atomic_under_concurrent_access(tmp_path: Path) -> None:
    """A cascade holds the store lock for the whole scan+rewrite, so concurrent
    reads never observe a half-rewritten set and the final state is consistent."""
    store = AgentProfileStore(base_dir=tmp_path)
    num = 20
    for i in range(num):
        store.save(_oh(f"p{i}", "default"))

    errors: list[Exception] = []

    def reader() -> None:
        try:
            for _ in range(20):
                # Every profile points at exactly one of the two names.
                find_referrers(store, "default")
                find_referrers(store, "renamed")
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    def renamer() -> None:
        try:
            cascade_rename(store, "default", "renamed")
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(reader) for _ in range(4)]
        futures.append(executor.submit(renamer))
        concurrent.futures.wait(futures)

    assert errors == []
    assert find_referrers(store, "default") == []
    assert sorted(find_referrers(store, "renamed")) == sorted(
        f"p{i}" for i in range(num)
    )
