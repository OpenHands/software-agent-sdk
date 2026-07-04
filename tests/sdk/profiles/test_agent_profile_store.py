"""Tests for ``AgentProfileStore`` — mirrors the ``LLMProfileStore`` suite.

Adds the profile-specific contracts: both union variants round-trip, the
``id`` is stable across rename, ``list_summaries`` projects the FK fields
without instantiating secrets, and ``skills[].mcp_tools`` secrets are redacted
by default / encrypted (never cleartext) under a cipher.
"""

import concurrent.futures
import json
import re
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentProfileStore,
    OpenHandsAgentProfile,
    ProfileLimitExceeded,
)
from openhands.sdk.profiles.agent_profile import AGENT_PROFILE_SCHEMA_VERSION
from openhands.sdk.skills import Skill
from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher


# A clearly identifiable secret carried by a skill's mcp_tools env/headers.
_MCP_SECRET = "ghp_SUPER_SECRET_TOKEN_SHOULD_NOT_LEAK"


@pytest.fixture
def agent_store(tmp_path: Path) -> AgentProfileStore:
    return AgentProfileStore(base_dir=tmp_path)


@pytest.fixture
def openhands_profile() -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name="oh",
        llm_profile_ref="default",
        revision=2,
        mcp_server_refs=["fetch"],
    )


@pytest.fixture
def acp_profile() -> ACPAgentProfile:
    return ACPAgentProfile(name="acp", acp_server="codex", acp_model="gpt-5.5/medium")


def _skill_with_secret() -> Skill:
    return Skill(
        name="leaky",
        content="do stuff",
        mcp_tools={
            "svc": MCPServer(
                url="https://x.test",
                headers={"Authorization": SecretStr(f"Bearer {_MCP_SECRET}")},
                env={"API_KEY": SecretStr(_MCP_SECRET)},
            )
        },
    )


def _profile_with_secret_skill(name: str = "secretful") -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name=name,
        llm_profile_ref="default",
        skills=[_skill_with_secret()],
    )


# ── Init ────────────────────────────────────────────────────────────────────


def test_init_creates_directory(tmp_path: Path) -> None:
    profile_dir = tmp_path / "agent-profiles"
    assert not profile_dir.exists()

    AgentProfileStore(base_dir=profile_dir)

    assert profile_dir.exists()
    assert profile_dir.is_dir()


def test_init_with_string_path(tmp_path: Path) -> None:
    profile_dir = str(tmp_path / "agent-profiles")
    store = AgentProfileStore(base_dir=profile_dir)

    assert store.base_dir == Path(profile_dir)
    assert store.base_dir.exists()


def test_init_with_existing_directory(tmp_path: Path) -> None:
    profile_dir = tmp_path / "agent-profiles"
    profile_dir.mkdir()

    store = AgentProfileStore(base_dir=profile_dir)
    assert store.base_dir == profile_dir


# ── List ────────────────────────────────────────────────────────────────────


def test_list_empty_store(agent_store: AgentProfileStore) -> None:
    assert agent_store.list() == []


def test_list_with_profiles(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    agent_store.save(openhands_profile.model_copy(update={"name": "oh2"}))

    profiles = agent_store.list()
    assert len(profiles) == 2
    assert "oh.json" in profiles
    assert "oh2.json" in profiles


def test_list_excludes_non_json_files(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    (agent_store.base_dir / "not_a_profile.txt").write_text("hello")

    assert agent_store.list() == ["oh.json"]


# ── Save ────────────────────────────────────────────────────────────────────


def test_save_creates_file(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    assert (agent_store.base_dir / "oh.json").exists()


def test_save_writes_schema_version(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    data = json.loads((agent_store.base_dir / "oh.json").read_text())
    assert data["schema_version"] == AGENT_PROFILE_SCHEMA_VERSION


def test_save_persists_id_inside_file(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    data = json.loads((agent_store.base_dir / "oh.json").read_text())
    assert data["id"] == str(openhands_profile.id)


def test_load_rejects_newer_schema_version(agent_store: AgentProfileStore) -> None:
    (agent_store.base_dir / "future.json").write_text(
        json.dumps(
            {
                "schema_version": AGENT_PROFILE_SCHEMA_VERSION + 1,
                "name": "future",
                "llm_profile_ref": "default",
            }
        )
    )
    with pytest.raises(ValueError, match="newer than supported"):
        agent_store.load("future")


@pytest.mark.parametrize(
    "name",
    [
        # Empty names are rejected by the model (``name`` has min_length=1), so
        # only names the model accepts but the store rejects are covered here.
        ".",
        "..",
        "my/profile",
        ".leading-dot",
        "-leading-dash",
        "name with space",
        "name@symbol",
        "a" * 65,
    ],
)
def test_save_with_invalid_profile_name(
    name: str, agent_store: AgentProfileStore
) -> None:
    profile = OpenHandsAgentProfile(name=name, llm_profile_ref="default")
    with pytest.raises(ValueError, match=re.escape(f"Invalid profile name: {name!r}.")):
        agent_store.save(profile)


def test_save_writes_valid_json(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    data = json.loads((agent_store.base_dir / "oh.json").read_text())
    assert data["agent_kind"] == "openhands"
    assert data["llm_profile_ref"] == "default"
    assert data["mcp_server_refs"] == ["fetch"]


def test_save_overwrites_existing(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    agent_store.save(openhands_profile.model_copy(update={"llm_profile_ref": "other"}))

    loaded = agent_store.load("oh")
    assert isinstance(loaded, OpenHandsAgentProfile)
    assert loaded.llm_profile_ref == "other"


# ── Cipher round-trip / secret-at-rest ──────────────────────────────────────


def test_save_without_cipher_redacts_skill_secret(
    agent_store: AgentProfileStore,
) -> None:
    agent_store.save(_profile_with_secret_skill())

    content = (agent_store.base_dir / "secretful.json").read_text()
    assert _MCP_SECRET not in content


def test_save_with_cipher_encrypts_skill_secret(
    agent_store: AgentProfileStore,
) -> None:
    cipher = Cipher(secret_key="test-key")
    agent_store.save(_profile_with_secret_skill(), cipher=cipher)

    content = (agent_store.base_dir / "secretful.json").read_text()
    # No cleartext, but recoverable: the stored token decrypts back via cipher.
    assert _MCP_SECRET not in content
    assert FERNET_TOKEN_PREFIX in content

    data = json.loads(content)
    env = data["skills"][0]["mcp_tools"]["svc"]["env"]
    token = env["API_KEY"]
    assert token != _MCP_SECRET
    decrypted = cipher.decrypt(token)
    assert decrypted is not None
    assert decrypted.get_secret_value() == _MCP_SECRET


def test_roundtrip_openhands_without_cipher(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    loaded = agent_store.load("oh")

    assert isinstance(loaded, OpenHandsAgentProfile)
    assert loaded.id == openhands_profile.id
    assert loaded.llm_profile_ref == "default"
    assert loaded.mcp_server_refs == ["fetch"]
    assert loaded.revision == 2


def test_roundtrip_acp_without_cipher(
    agent_store: AgentProfileStore, acp_profile: ACPAgentProfile
) -> None:
    agent_store.save(acp_profile)
    loaded = agent_store.load("acp")

    assert isinstance(loaded, ACPAgentProfile)
    assert loaded.id == acp_profile.id
    assert loaded.acp_server == "codex"
    assert loaded.acp_model == "gpt-5.5/medium"


def test_roundtrip_with_cipher_preserves_non_secret_fields(
    agent_store: AgentProfileStore,
) -> None:
    cipher = Cipher(secret_key="test-key")
    profile = _profile_with_secret_skill()
    agent_store.save(profile, cipher=cipher)
    loaded = agent_store.load("secretful", cipher=cipher)

    assert isinstance(loaded, OpenHandsAgentProfile)
    assert loaded.id == profile.id
    assert loaded.llm_profile_ref == "default"
    assert loaded.skills[0].name == "leaky"


def test_load_with_cipher_decrypts_skill_secret(
    agent_store: AgentProfileStore,
) -> None:
    cipher = Cipher(secret_key="test-key")
    agent_store.save(_profile_with_secret_skill(), cipher=cipher)
    loaded = agent_store.load("secretful", cipher=cipher)

    assert isinstance(loaded, OpenHandsAgentProfile)
    mcp_tools = loaded.skills[0].mcp_tools
    assert mcp_tools is not None
    env = mcp_tools["svc"].env
    assert env is not None
    token = env["API_KEY"]
    assert token.get_secret_value() == _MCP_SECRET


# ── Load ────────────────────────────────────────────────────────────────────


def test_load_nonexistent_profile(agent_store: AgentProfileStore) -> None:
    with pytest.raises(FileNotFoundError) as exc_info:
        agent_store.load("nonexistent")
    assert "nonexistent" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_load_nonexistent_shows_available(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    with pytest.raises(FileNotFoundError) as exc_info:
        agent_store.load("nonexistent")
    assert "oh.json" in str(exc_info.value)


def test_load_corrupted_profile(agent_store: AgentProfileStore) -> None:
    (agent_store.base_dir / "corrupted.json").write_text("{ invalid json }")
    with pytest.raises(ValueError) as exc_info:
        agent_store.load("corrupted")
    assert "Failed to load profile" in str(exc_info.value)


# ── Delete ──────────────────────────────────────────────────────────────────


def test_delete_existing_profile(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    assert "oh.json" in agent_store.list()

    agent_store.delete("oh")
    assert "oh.json" not in agent_store.list()


def test_delete_nonexistent_profile(agent_store: AgentProfileStore) -> None:
    agent_store.delete("nonexistent")


# ── Rename ──────────────────────────────────────────────────────────────────


def test_rename_moves_file(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    agent_store.rename("oh", "renamed")

    assert (agent_store.base_dir / "renamed.json").exists()
    assert not (agent_store.base_dir / "oh.json").exists()


def test_rename_syncs_internal_name_and_preserves_id(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    original_id = openhands_profile.id
    agent_store.rename("oh", "renamed")

    loaded = agent_store.load("renamed")
    assert loaded.name == "renamed"
    assert loaded.id == original_id


def test_rename_source_missing_raises(agent_store: AgentProfileStore) -> None:
    with pytest.raises(FileNotFoundError, match="missing"):
        agent_store.rename("missing", "anywhere")


def test_rename_target_exists_raises(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    agent_store.save(openhands_profile.model_copy(update={"name": "taken"}))

    with pytest.raises(FileExistsError, match="taken"):
        agent_store.rename("oh", "taken")

    assert (agent_store.base_dir / "oh.json").exists()
    assert (agent_store.base_dir / "taken.json").exists()


def test_rename_same_name_is_noop(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    agent_store.rename("oh", "oh")
    assert agent_store.list() == ["oh.json"]


def test_rename_same_name_missing_raises(agent_store: AgentProfileStore) -> None:
    with pytest.raises(FileNotFoundError, match="ghost"):
        agent_store.rename("ghost", "ghost")


def test_rename_invalid_name_raises(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    with pytest.raises(ValueError, match="Invalid profile name"):
        agent_store.rename("oh", "../escape")


# ── list_summaries ──────────────────────────────────────────────────────────


def test_list_summaries_empty(agent_store: AgentProfileStore) -> None:
    assert agent_store.list_summaries() == []


def test_list_summaries_returns_fk_fields(
    agent_store: AgentProfileStore,
    openhands_profile: OpenHandsAgentProfile,
    acp_profile: ACPAgentProfile,
) -> None:
    agent_store.save(openhands_profile)
    agent_store.save(acp_profile)

    by_name = {s["name"]: s for s in agent_store.list_summaries()}

    oh = by_name["oh"]
    assert oh["id"] == str(openhands_profile.id)
    assert oh["agent_kind"] == "openhands"
    assert oh["revision"] == 2
    assert oh["llm_profile_ref"] == "default"
    assert oh["mcp_server_refs"] == ["fetch"]

    acp = by_name["acp"]
    assert acp["agent_kind"] == "acp"
    # ACP profiles have no llm_profile_ref.
    assert acp["llm_profile_ref"] is None


def test_list_summaries_does_not_decrypt_secrets(
    agent_store: AgentProfileStore,
) -> None:
    cipher = Cipher(secret_key="test-key")
    agent_store.save(_profile_with_secret_skill(), cipher=cipher)

    summaries = agent_store.list_summaries()
    # No skill content / secret is loaded — only the FK projection.
    assert _MCP_SECRET not in json.dumps(summaries)
    assert summaries[0]["name"] == "secretful"
    assert "skills" not in summaries[0]


def test_list_summaries_skips_corrupted(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    (agent_store.base_dir / "bad.json").write_text("{ not valid json")

    assert [s["name"] for s in agent_store.list_summaries()] == ["oh"]


def test_list_summaries_skips_non_dict(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    (agent_store.base_dir / "list.json").write_text("[1, 2, 3]")

    assert [s["name"] for s in agent_store.list_summaries()] == ["oh"]


def test_list_summaries_skips_invalid_filename(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile)
    (agent_store.base_dir / ".hidden.json").write_text('{"name": "x"}')
    (agent_store.base_dir / "bad@name.json").write_text('{"name": "x"}')

    assert [s["name"] for s in agent_store.list_summaries()] == ["oh"]


# ── max_profiles ────────────────────────────────────────────────────────────


def test_save_with_max_profiles_blocks_over_limit(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile.model_copy(update={"name": "a"}))
    agent_store.save(openhands_profile.model_copy(update={"name": "b"}))

    with pytest.raises(ProfileLimitExceeded, match="2"):
        agent_store.save(
            openhands_profile.model_copy(update={"name": "c"}), max_profiles=2
        )


def test_save_with_max_profiles_allows_overwrite(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile.model_copy(update={"name": "a"}))
    agent_store.save(openhands_profile.model_copy(update={"name": "b"}))

    agent_store.save(openhands_profile.model_copy(update={"name": "a"}), max_profiles=2)
    assert len(agent_store.list()) == 2


def test_save_with_max_profiles_ignores_invalid_filenames(
    agent_store: AgentProfileStore, openhands_profile: OpenHandsAgentProfile
) -> None:
    agent_store.save(openhands_profile.model_copy(update={"name": "real"}))
    (agent_store.base_dir / ".hidden.json").write_text('{"name": "x"}')
    (agent_store.base_dir / "bad@name.json").write_text('{"name": "x"}')

    agent_store.save(
        openhands_profile.model_copy(update={"name": "another"}), max_profiles=2
    )
    assert "another.json" in agent_store.list()


def test_save_cleans_up_tmp_on_replace_failure(
    agent_store: AgentProfileStore,
    openhands_profile: OpenHandsAgentProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        agent_store.save(openhands_profile)

    assert list(agent_store.base_dir.glob("*.tmp")) == []


# ── Concurrency ─────────────────────────────────────────────────────────────


def test_concurrent_saves(tmp_path: Path) -> None:
    store = AgentProfileStore(base_dir=tmp_path)
    num_threads = 10
    results: list[int] = []
    errors: list[tuple[int, Exception]] = []

    def save_profile(index: int) -> None:
        try:
            store.save(
                OpenHandsAgentProfile(
                    name=f"profile_{index}", llm_profile_ref=f"llm_{index}"
                )
            )
            results.append(index)
        except Exception as e:
            errors.append((index, e))

    threads = [
        threading.Thread(target=save_profile, args=(i,)) for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == num_threads
    assert len(store.list()) == num_threads


def test_concurrent_reads_and_writes(tmp_path: Path) -> None:
    store = AgentProfileStore(base_dir=tmp_path)
    for i in range(5):
        store.save(
            OpenHandsAgentProfile(name=f"profile_{i}", llm_profile_ref=f"llm_{i}")
        )

    errors: list[tuple[str, str | int, Exception]] = []
    read_results: list[str] = []
    write_results: list[int] = []

    def read_profile(name: str) -> None:
        try:
            loaded = store.load(name)
            read_results.append(loaded.name)
        except Exception as e:
            errors.append(("read", name, e))

    def write_profile(index: int) -> None:
        try:
            store.save(
                OpenHandsAgentProfile(name=f"new_profile_{index}", llm_profile_ref="x")
            )
            write_results.append(index)
        except Exception as e:
            errors.append(("write", index, e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for i in range(5):
            futures.append(executor.submit(read_profile, f"profile_{i}"))
        for i in range(5):
            futures.append(executor.submit(write_profile, i))
        concurrent.futures.wait(futures)

    assert errors == []
    assert len(read_results) == 5
    assert len(write_results) == 5


def test_full_workflow(agent_store: AgentProfileStore) -> None:
    profile = OpenHandsAgentProfile(name="wf", llm_profile_ref="default", revision=1)

    agent_store.save(profile)
    assert "wf.json" in agent_store.list()

    loaded = agent_store.load("wf")
    assert loaded.id == profile.id
    assert isinstance(loaded, OpenHandsAgentProfile)
    assert loaded.llm_profile_ref == "default"

    agent_store.rename("wf", "wf2")
    assert agent_store.load("wf2").id == profile.id

    agent_store.delete("wf2")
    assert agent_store.list() == []
