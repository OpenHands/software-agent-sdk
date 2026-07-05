"""The store-agnostic prereqs for the cloud AgentProfiles surface (#3730).

Proves the FK helpers (``profile_refs``), the id/revision lifecycle
(``save_profile_preserving_identity``), and the seed/422 hoists work against a
**non-file** store — i.e. a backend that satisfies ``AgentProfileStoreProtocol``
without ``base_dir`` / ``_atomic_write``, the way a cloud DB-backed store will.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from openhands.sdk.profiles import (
    SEED_PROFILE_NAME,
    ACPAgentProfile,
    AgentProfileStoreProtocol,
    OpenHandsAgentProfile,
    ProfileLimitExceeded,
    ProfileReferenced,
    ProfileVerificationSettings,
    build_profile_verification,
    build_seed_profile,
    cascade_rename,
    delete_llm_profile,
    find_referrers,
    rename_llm_profile,
    safe_validation_error_detail,
    save_profile_preserving_identity,
    validate_agent_profile,
)
from openhands.sdk.settings.model import VerificationSettings, validate_agent_settings


class InMemoryAgentProfileStore:
    """Minimal non-file ``AgentProfileStoreProtocol`` impl (the cloud envelope).

    Holds profiles in a dict keyed by name, exposes the metadata + write
    primitives the SDK FK / identity helpers need, and provides a re-entrant
    no-op ``lock`` (a cloud store's router holds the row transaction instead).
    """

    def __init__(self) -> None:
        self._profiles: dict[str, OpenHandsAgentProfile | ACPAgentProfile] = {}

    @contextmanager
    def lock(self, timeout: float = 30.0):
        yield  # re-entrant no-op

    def list(self) -> list[str]:
        return [f"{name}.json" for name in self._profiles]

    def list_summaries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, p in self._profiles.items():
            d = p.model_dump(mode="json")
            kind = d.get("agent_kind", "openhands")
            out.append(
                {
                    "id": d.get("id"),
                    "name": name,
                    "agent_kind": kind,
                    "revision": d.get("revision"),
                    "llm_profile_ref": (
                        d.get("llm_profile_ref") if kind == "openhands" else None
                    ),
                    "mcp_server_refs": d.get("mcp_server_refs"),
                }
            )
        return out

    def save(self, profile, *, cipher=None, max_profiles=None) -> None:
        if (
            max_profiles is not None
            and profile.name not in self._profiles
            and len(self._profiles) >= max_profiles
        ):
            raise ProfileLimitExceeded(f"Profile limit reached ({max_profiles}).")
        self._profiles[profile.name] = profile

    def load(self, name, *, cipher=None):
        if name not in self._profiles:
            raise FileNotFoundError(name)
        return self._profiles[name]

    def delete(self, name) -> None:
        self._profiles.pop(name, None)

    def rename(self, old_name, new_name) -> None:
        if old_name not in self._profiles:
            raise FileNotFoundError(old_name)
        if new_name in self._profiles:
            raise FileExistsError(new_name)
        p = self._profiles.pop(old_name)
        self._profiles[new_name] = p.model_copy(update={"name": new_name})

    def set_llm_profile_ref(self, name, new_ref) -> None:
        p = self._profiles.get(name)
        if isinstance(p, OpenHandsAgentProfile):
            self._profiles[name] = p.model_copy(update={"llm_profile_ref": new_ref})

    def name_for_id(self, profile_id) -> str | None:
        target = str(profile_id)
        for name, p in self._profiles.items():
            if str(p.id) == target:
                return name
        return None


class FakeLLMStore:
    """Stub LLM store exposing only ``delete`` / ``rename`` for the FK helpers."""

    def __init__(self, names: list[str]) -> None:
        self.names = list(names)

    def delete(self, name: str) -> None:
        if name in self.names:
            self.names.remove(name)

    def rename(self, old_name: str, new_name: str) -> None:
        if old_name not in self.names:
            raise FileNotFoundError(old_name)
        self.names[self.names.index(old_name)] = new_name


def _seed(store: InMemoryAgentProfileStore) -> None:
    save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="reviewer", llm_profile_ref="gpt")
    )
    save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="coder", llm_profile_ref="gpt")
    )
    save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="cheap", llm_profile_ref="haiku")
    )
    save_profile_preserving_identity(
        store, ACPAgentProfile(name="claude", acp_server="claude-code")
    )


def test_inmemory_store_satisfies_protocol():
    # runtime_checkable: a non-file backend is structurally interchangeable.
    assert isinstance(InMemoryAgentProfileStore(), AgentProfileStoreProtocol)


def test_find_referrers_over_protocol_store():
    store = InMemoryAgentProfileStore()
    _seed(store)
    assert sorted(find_referrers(store, "gpt")) == ["coder", "reviewer"]
    assert find_referrers(store, "haiku") == ["cheap"]
    # ACP profiles carry no llm_profile_ref and never match.
    assert find_referrers(store, "claude-code") == []
    assert find_referrers(store, "missing") == []


def test_cascade_rename_over_protocol_store():
    store = InMemoryAgentProfileStore()
    _seed(store)
    rewritten = cascade_rename(store, "gpt", "gpt-5")
    assert sorted(rewritten) == ["coder", "reviewer"]
    assert find_referrers(store, "gpt") == []
    assert sorted(find_referrers(store, "gpt-5")) == ["coder", "reviewer"]
    # The unrelated ref is untouched.
    assert find_referrers(store, "haiku") == ["cheap"]


def test_delete_llm_profile_blocks_on_referrers():
    store = InMemoryAgentProfileStore()
    _seed(store)
    llm_store = FakeLLMStore(["gpt", "haiku"])
    # 'gpt' is referenced by reviewer + coder -> blocked, llm profile untouched.
    with pytest.raises(ProfileReferenced) as exc:
        delete_llm_profile(store, llm_store, "gpt")
    assert sorted(exc.value.referrers) == ["coder", "reviewer"]
    assert "gpt" in llm_store.names

    # Detach the referrers; the delete then goes through.
    store.delete("reviewer")
    store.delete("coder")
    delete_llm_profile(store, llm_store, "gpt")
    assert "gpt" not in llm_store.names


def test_rename_llm_profile_cascades_over_protocol_store():
    store = InMemoryAgentProfileStore()
    _seed(store)
    llm_store = FakeLLMStore(["gpt", "haiku"])
    rewritten = rename_llm_profile(store, llm_store, "gpt", "gpt-5")
    assert sorted(rewritten) == ["coder", "reviewer"]
    assert "gpt-5" in llm_store.names and "gpt" not in llm_store.names
    assert sorted(find_referrers(store, "gpt-5")) == ["coder", "reviewer"]


def test_save_preserving_identity_mints_then_keeps_id_and_bumps_revision():
    store = InMemoryAgentProfileStore()
    created = save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="p", llm_profile_ref="gpt")
    )
    assert created.revision == 0
    first_id = created.id

    # Overwrite keeps the id and bumps revision; a client-supplied id is ignored.
    again = save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="p", llm_profile_ref="haiku")
    )
    assert again.id == first_id
    assert again.revision == 1
    reloaded = store.load("p")
    assert isinstance(reloaded, OpenHandsAgentProfile)
    assert reloaded.llm_profile_ref == "haiku"

    # A different name mints a fresh id.
    other = save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="q", llm_profile_ref="gpt")
    )
    assert other.id != first_id
    assert other.revision == 0


def test_save_preserving_identity_enforces_limit():
    store = InMemoryAgentProfileStore()
    save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="a", llm_profile_ref="gpt"), max_profiles=1
    )
    with pytest.raises(ProfileLimitExceeded):
        save_profile_preserving_identity(
            store,
            OpenHandsAgentProfile(name="b", llm_profile_ref="gpt"),
            max_profiles=1,
        )
    # Overwriting the existing one is allowed at the cap.
    save_profile_preserving_identity(
        store, OpenHandsAgentProfile(name="a", llm_profile_ref="haiku"), max_profiles=1
    )


def test_build_seed_profile_openhands_branch():
    settings = validate_agent_settings({"agent_kind": "openhands"})
    profile = build_seed_profile(settings, active_llm_profile="my-llm")
    assert isinstance(profile, OpenHandsAgentProfile)
    assert profile.name == SEED_PROFILE_NAME
    assert profile.llm_profile_ref == "my-llm"
    assert profile.mcp_server_refs is None
    # Secret-free verification projection (no critic_api_key on the profile type).
    assert "critic_api_key" not in type(profile.verification).model_fields

    # No active LLM profile -> soft fallback to SEED_PROFILE_NAME.
    fallback = build_seed_profile(settings, active_llm_profile=None)
    assert isinstance(fallback, OpenHandsAgentProfile)
    assert fallback.llm_profile_ref == SEED_PROFILE_NAME


def test_build_seed_profile_acp_branch():
    settings = validate_agent_settings(
        {"agent_kind": "acp", "acp_server": "claude-code"}
    )
    profile = build_seed_profile(settings, active_llm_profile="ignored")
    assert isinstance(profile, ACPAgentProfile)
    assert profile.name == SEED_PROFILE_NAME
    assert profile.acp_server == "claude-code"
    assert profile.mcp_server_refs is None
    assert not hasattr(profile, "llm_profile_ref")


def test_build_profile_verification_drops_critic_api_key():
    v = VerificationSettings(
        critic_enabled=True,
        critic_threshold=0.9,
        critic_model_name="some-model",
    )
    projected = build_profile_verification(v)
    assert isinstance(projected, ProfileVerificationSettings)
    assert projected.critic_enabled is True
    assert projected.critic_threshold == 0.9
    assert projected.critic_model_name == "some-model"
    assert "critic_api_key" not in ProfileVerificationSettings.model_fields


def test_safe_validation_error_detail_is_secret_safe():
    secret = "sk-super-secret-value"
    with pytest.raises(Exception) as exc_info:
        # extra=forbid -> the unexpected key (carrying a secret) triggers a
        # ValidationError that embeds the input in msg/input.
        validate_agent_profile({"name": "p", "llm_profile_ref": "gpt", "leak": secret})
    from pydantic import ValidationError

    assert isinstance(exc_info.value, ValidationError)
    detail = safe_validation_error_detail(exc_info.value)
    assert detail, "expected at least one error entry"
    for entry in detail:
        assert set(entry.keys()) == {"loc", "type"}
    # The secret never appears in the redacted detail.
    assert secret not in repr(detail)
