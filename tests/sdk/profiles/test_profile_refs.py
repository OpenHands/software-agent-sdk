"""Tests for the LLM-profile <- AgentProfile foreign-key lifecycle."""

import concurrent.futures
import json
from pathlib import Path

import pytest

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles import (
    SEED_PROFILE_NAME,
    ACPAgentProfile,
    AgentProfileStore,
    OpenHandsAgentProfile,
    ProfileReferenced,
    cascade_rename,
    delete_llm_profile,
    find_referrers,
    rename_llm_profile,
    sync_seed_llm_ref,
)


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


def test_cascade_rename_preserves_id_and_other_fields(
    agent_store: AgentProfileStore,
) -> None:
    """The surgical raw-JSON edit (``set_llm_profile_ref``) only touches the
    ref field — id, mcp_server_refs, and everything else survive untouched.
    No cipher is involved: the profile is secret-free at rest (#4017)."""
    profile = OpenHandsAgentProfile(
        name="a", llm_profile_ref="default", mcp_server_refs=["fetch"], revision=3
    )
    agent_store.save(profile)

    cascade_rename(agent_store, "default", "renamed")

    raw = (agent_store.base_dir / "a.json").read_text()
    data = json.loads(raw)
    assert data["id"] == str(profile.id)
    assert data["llm_profile_ref"] == "renamed"
    assert data["mcp_server_refs"] == ["fetch"]
    assert data["revision"] == 3


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


# ── sync_seed_llm_ref ───────────────────────────────────────────────────────


def test_sync_seed_llm_ref_repoints_when_in_sync(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(_oh(SEED_PROFILE_NAME, "profile-a"))

    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-b")

    assert result is True
    assert _ref(agent_store, SEED_PROFILE_NAME) == "profile-b"


def test_sync_seed_llm_ref_does_not_clobber_pinned_ref(
    agent_store: AgentProfileStore,
) -> None:
    """The anti-clobber guarantee: a seed ref that has drifted away from
    ``old_ref`` was deliberately pinned by the user and must never be
    overwritten by an activation-triggered sync (#4338)."""
    agent_store.save(_oh(SEED_PROFILE_NAME, "profile-c"))

    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-b")

    assert result is False
    assert _ref(agent_store, SEED_PROFILE_NAME) == "profile-c"


def test_sync_seed_llm_ref_repoints_dangling_soft_ref(
    agent_store: AgentProfileStore,
) -> None:
    """A fresh instance seeds its default profile with
    ``llm_profile_ref == SEED_PROFILE_NAME`` before any LLM profile named
    ``"default"`` necessarily exists (#3933). When that name is not among the
    known LLM profiles, the ref is a dangling soft-ref, not a pin."""
    agent_store.save(_oh(SEED_PROFILE_NAME, SEED_PROFILE_NAME))

    result = sync_seed_llm_ref(
        agent_store,
        old_ref=None,
        new_ref="profile-b",
        known_llm_profiles=set(),
    )

    assert result is True
    assert _ref(agent_store, SEED_PROFILE_NAME) == "profile-b"


def test_sync_seed_llm_ref_leaves_real_pin_matching_seed_name(
    agent_store: AgentProfileStore,
) -> None:
    """If ``SEED_PROFILE_NAME`` resolves to a real, known LLM profile, the ref
    is a genuine pin (not the #3933 dangling default) and must be left alone."""
    agent_store.save(_oh(SEED_PROFILE_NAME, SEED_PROFILE_NAME))

    result = sync_seed_llm_ref(
        agent_store,
        old_ref="profile-a",
        new_ref="profile-b",
        known_llm_profiles={SEED_PROFILE_NAME, "profile-a"},
    )

    assert result is False
    assert _ref(agent_store, SEED_PROFILE_NAME) == SEED_PROFILE_NAME


def test_sync_seed_llm_ref_acp_profile_returns_false(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(ACPAgentProfile(name=SEED_PROFILE_NAME, acp_server="codex"))
    raw_before = (agent_store.base_dir / f"{SEED_PROFILE_NAME}.json").read_text()

    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-b")

    assert result is False
    raw_after = (agent_store.base_dir / f"{SEED_PROFILE_NAME}.json").read_text()
    assert raw_after == raw_before


def test_sync_seed_llm_ref_empty_store_returns_false(
    agent_store: AgentProfileStore,
) -> None:
    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-b")
    assert result is False


def test_sync_seed_llm_ref_noop_when_ref_already_matches_new(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(_oh(SEED_PROFILE_NAME, "profile-a"))
    path = agent_store.base_dir / f"{SEED_PROFILE_NAME}.json"
    mtime_before = path.stat().st_mtime_ns

    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-a")

    assert result is False
    assert path.stat().st_mtime_ns == mtime_before


def test_sync_seed_llm_ref_only_touches_seed_profile(
    agent_store: AgentProfileStore,
) -> None:
    """Proves narrow targeting: a namesake with the same stale ref but a
    different name is never touched, even though it would match a broader
    ``cascade_rename``-style scan."""
    agent_store.save(_oh(SEED_PROFILE_NAME, "profile-a"))
    agent_store.save(_oh("other", "profile-a"))

    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-b")

    assert result is True
    assert _ref(agent_store, SEED_PROFILE_NAME) == "profile-b"
    assert _ref(agent_store, "other") == "profile-a"


def test_sync_seed_llm_ref_preserves_id_and_revision(
    agent_store: AgentProfileStore,
) -> None:
    before = OpenHandsAgentProfile(
        name=SEED_PROFILE_NAME, llm_profile_ref="profile-a", revision=5
    )
    agent_store.save(before)

    result = sync_seed_llm_ref(agent_store, old_ref="profile-a", new_ref="profile-b")

    assert result is True
    after = agent_store.load(SEED_PROFILE_NAME)
    assert isinstance(after, OpenHandsAgentProfile)
    assert after.id == before.id
    assert after.revision == before.revision
