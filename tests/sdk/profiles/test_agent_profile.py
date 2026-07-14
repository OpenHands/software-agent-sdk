"""Tests for the ``AgentProfile`` kind-discriminated union.

Mirrors the ``AgentSettingsConfig`` union tests in ``tests/sdk/test_settings.py``:
round-trip both variants, confirm narrowing on ``agent_kind``, confirm the
cross-variant fields are rejected, and confirm the ``mcp_server_refs`` null/[]
distinction. Adds the profile-specific contract: secret-free at rest.
"""

import json
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from openhands.sdk.profiles import (
    AGENT_PROFILE_SCHEMA_VERSION,
    ACPAgentProfile,
    AgentProfile,
    OpenHandsAgentProfile,
    validate_agent_profile,
)


_ADAPTER: TypeAdapter[OpenHandsAgentProfile | ACPAgentProfile] = TypeAdapter(
    AgentProfile
)


# ---------------------------------------------------------------------------
# Construction + round-trip
# ---------------------------------------------------------------------------


def test_openhands_profile_round_trips() -> None:
    profile = OpenHandsAgentProfile(
        name="my-openhands",
        llm_profile_ref="default",
        revision=3,
        mcp_server_refs=["fetch"],
        disabled_skills=["pdf-tools"],
        system_message_suffix="be terse",
        enable_sub_agents=True,
        enable_switch_llm_tool=False,
        tool_concurrency_limit=4,
    )
    reloaded = validate_agent_profile(profile.model_dump(mode="json"))

    assert isinstance(reloaded, OpenHandsAgentProfile)
    assert reloaded == profile
    assert reloaded.agent_kind == "openhands"
    assert reloaded.agent == "CodeActAgent"
    assert reloaded.llm_profile_ref == "default"
    assert reloaded.revision == 3
    assert reloaded.mcp_server_refs == ["fetch"]
    assert reloaded.disabled_skills == ["pdf-tools"]
    assert reloaded.enable_switch_llm_tool is False
    assert reloaded.tool_concurrency_limit == 4


def test_openhands_profile_new_field_defaults() -> None:
    """``enable_switch_llm_tool`` defaults True (global parity); ``disabled_skills``
    defaults ``[]`` — the deny-list starts empty, so an unset field means "all
    discovered skills" (#4017). Skills are selected by exclusion, never an
    allow-list of names that could dangle."""
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    assert profile.enable_switch_llm_tool is True
    assert profile.disabled_skills == []
    reloaded = validate_agent_profile(
        {"agent_kind": "openhands", "name": "oh", "llm_profile_ref": "default"}
    )
    assert isinstance(reloaded, OpenHandsAgentProfile)
    assert reloaded.enable_switch_llm_tool is True
    assert reloaded.disabled_skills == []


def test_default_profile_preserves_explicit_empty_tools() -> None:
    profile = OpenHandsAgentProfile(
        name="default", llm_profile_ref="default", revision=0, tools=[]
    )

    assert profile.tools == []


def test_disabled_skills_round_trips() -> None:
    """A non-empty deny-list survives the JSON round-trip verbatim."""
    profile = validate_agent_profile(
        OpenHandsAgentProfile(
            name="oh", llm_profile_ref="default", disabled_skills=["a", "b"]
        ).model_dump(mode="json")
    )
    assert isinstance(profile, OpenHandsAgentProfile)
    assert profile.disabled_skills == ["a", "b"]


def test_acp_profile_has_no_skill_field() -> None:
    """ACP profiles carry no skill-selection field at all — the subprocess owns
    its tooling and prompt context (#4017). ``extra="forbid"`` rejects a stray
    ``skill_refs``/``disabled_skills`` on an ACP payload."""
    from openhands.sdk.profiles import ACPAgentProfile

    profile = ACPAgentProfile(name="acp", acp_server="claude-code")
    assert not hasattr(profile, "skill_refs")
    assert not hasattr(profile, "disabled_skills")
    with pytest.raises(ValidationError):
        validate_agent_profile(
            {
                "agent_kind": "acp",
                "name": "acp",
                "acp_server": "claude-code",
                "disabled_skills": ["x"],
            }
        )


def test_acp_profile_round_trips() -> None:
    profile = ACPAgentProfile(
        name="my-acp",
        acp_server="codex",
        acp_model="gpt-5.5/medium",
        acp_session_mode="full-access",
        acp_prompt_timeout=600.0,
        acp_command="codex-acp",
        acp_args=["--flag"],
        mcp_server_refs=None,
    )
    reloaded = validate_agent_profile(profile.model_dump(mode="json"))

    assert isinstance(reloaded, ACPAgentProfile)
    assert reloaded == profile
    assert reloaded.agent_kind == "acp"
    assert reloaded.acp_server == "codex"
    assert reloaded.acp_model == "gpt-5.5/medium"
    assert reloaded.acp_command == "codex-acp"
    assert reloaded.acp_args == ["--flag"]
    assert reloaded.mcp_server_refs is None


def test_acp_profile_minimal_defaults() -> None:
    profile = validate_agent_profile({"agent_kind": "acp", "name": "minimal"})

    assert isinstance(profile, ACPAgentProfile)
    assert profile.acp_server == "claude-code"
    assert profile.acp_model is None
    assert profile.acp_session_mode is None
    assert profile.acp_prompt_timeout == 1800.0
    assert profile.acp_command is None
    assert profile.acp_args is None


# ---------------------------------------------------------------------------
# Discriminator + validation
# ---------------------------------------------------------------------------


def test_validate_dispatches_on_agent_kind() -> None:
    openhands = validate_agent_profile(
        {"agent_kind": "openhands", "name": "oh", "llm_profile_ref": "default"}
    )
    assert isinstance(openhands, OpenHandsAgentProfile)
    assert openhands.agent_kind == "openhands"

    acp = validate_agent_profile(
        {"agent_kind": "acp", "name": "acp", "acp_model": "claude-opus-4-8"}
    )
    assert isinstance(acp, ACPAgentProfile)
    assert acp.agent_kind == "acp"


def test_missing_discriminator_defaults_to_openhands() -> None:
    profile = validate_agent_profile({"name": "oh", "llm_profile_ref": "default"})
    assert isinstance(profile, OpenHandsAgentProfile)
    assert profile.agent_kind == "openhands"


def test_type_adapter_narrows_directly() -> None:
    """A bare ``TypeAdapter(AgentProfile)`` (no migration) narrows correctly."""
    acp = _ADAPTER.validate_python({"agent_kind": "acp", "name": "acp"})
    assert isinstance(acp, ACPAgentProfile)


def test_validate_passes_through_instances() -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="default")
    assert validate_agent_profile(profile) is profile


def test_validate_rejects_non_mapping() -> None:
    with pytest.raises(TypeError, match="must be a mapping or BaseModel"):
        validate_agent_profile(["not", "a", "mapping"])


# ---------------------------------------------------------------------------
# Cross-variant field rejection (extra="forbid")
# ---------------------------------------------------------------------------


def test_acp_rejects_llm_profile_ref() -> None:
    with pytest.raises(ValidationError):
        validate_agent_profile(
            {"agent_kind": "acp", "name": "acp", "llm_profile_ref": "default"}
        )


def test_openhands_rejects_acp_fields() -> None:
    for acp_field, value in (
        ("acp_server", "codex"),
        ("acp_model", "gpt-5.5/medium"),
        ("acp_command", "codex-acp"),
        ("acp_args", ["--flag"]),
        ("acp_session_mode", "full-access"),
        ("acp_prompt_timeout", 600.0),
    ):
        with pytest.raises(ValidationError):
            validate_agent_profile(
                {
                    "agent_kind": "openhands",
                    "name": "oh",
                    "llm_profile_ref": "default",
                    acp_field: value,
                }
            )


def test_openhands_requires_llm_profile_ref() -> None:
    with pytest.raises(ValidationError):
        validate_agent_profile({"agent_kind": "openhands", "name": "oh"})


def test_acp_rejects_unknown_acp_server() -> None:
    with pytest.raises(ValidationError):
        validate_agent_profile(
            {"agent_kind": "acp", "name": "acp", "acp_server": "not-a-provider"}
        )


# ---------------------------------------------------------------------------
# mcp_server_refs: null vs [] are distinct
# ---------------------------------------------------------------------------


def test_mcp_server_refs_null_vs_empty_are_distinct() -> None:
    use_all = validate_agent_profile(
        {"name": "a", "llm_profile_ref": "d", "mcp_server_refs": None}
    )
    use_none = validate_agent_profile(
        {"name": "b", "llm_profile_ref": "d", "mcp_server_refs": []}
    )
    subset = validate_agent_profile(
        {"name": "c", "llm_profile_ref": "d", "mcp_server_refs": ["fetch"]}
    )

    assert use_all.mcp_server_refs is None
    assert use_none.mcp_server_refs == []
    assert subset.mcp_server_refs == ["fetch"]

    # The distinction must survive a serialize → reload round-trip.
    assert (
        validate_agent_profile(use_all.model_dump(mode="json")).mcp_server_refs is None
    )
    assert (
        validate_agent_profile(use_none.model_dump(mode="json")).mcp_server_refs == []
    )


def test_mcp_server_refs_default_is_null() -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="d")
    assert profile.mcp_server_refs is None


# ---------------------------------------------------------------------------
# schema_version + migration
# ---------------------------------------------------------------------------


def test_schema_version_defaults_to_current() -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="d")
    assert profile.schema_version == AGENT_PROFILE_SCHEMA_VERSION


def test_payload_missing_schema_version_canonicalizes() -> None:
    payload = {"agent_kind": "acp", "name": "acp"}
    assert "schema_version" not in payload
    profile = validate_agent_profile(payload)
    assert profile.schema_version == AGENT_PROFILE_SCHEMA_VERSION


def test_rejects_newer_schema_version() -> None:
    with pytest.raises(ValueError, match="newer than supported"):
        validate_agent_profile(
            {
                "name": "oh",
                "llm_profile_ref": "d",
                "schema_version": AGENT_PROFILE_SCHEMA_VERSION + 1,
            }
        )


def test_rejects_non_integer_schema_version() -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        validate_agent_profile(
            {"name": "oh", "llm_profile_ref": "d", "schema_version": "1"}
        )


def test_rejects_negative_schema_version() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        validate_agent_profile(
            {"name": "oh", "llm_profile_ref": "d", "schema_version": -1}
        )


# ---------------------------------------------------------------------------
# Identity: id (stable UUID) vs name (renameable)
# ---------------------------------------------------------------------------


def test_id_is_uuid_and_autogenerated() -> None:
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="d")
    assert isinstance(profile.id, UUID)
    other = OpenHandsAgentProfile(name="oh", llm_profile_ref="d")
    assert profile.id != other.id


def test_explicit_id_is_preserved_across_round_trip() -> None:
    fixed = uuid4()
    profile = validate_agent_profile(
        {"name": "oh", "llm_profile_ref": "d", "id": str(fixed)}
    )
    assert profile.id == fixed
    assert validate_agent_profile(profile.model_dump(mode="json")).id == fixed


def test_name_is_required() -> None:
    with pytest.raises(ValidationError):
        validate_agent_profile({"llm_profile_ref": "d"})


# ---------------------------------------------------------------------------
# Secret-free at rest
# ---------------------------------------------------------------------------


def test_openhands_profile_persists_no_secret_fields() -> None:
    dumped = OpenHandsAgentProfile(name="oh", llm_profile_ref="default").model_dump(
        mode="json"
    )
    # The profile carries a *reference*, never the credential itself.
    assert "llm" not in dumped
    assert "api_key" not in dumped
    assert "llm_profile_ref" in dumped


def test_acp_profile_persists_no_secret_fields() -> None:
    dumped = ACPAgentProfile(name="acp", acp_server="claude-code").model_dump(
        mode="json"
    )
    # No embedded credential and no secret bag on the profile.
    for key in ("llm", "api_key", "secrets", "agent_context"):
        assert key not in dumped


def test_verification_field_cannot_carry_a_secret() -> None:
    """The verification block is secret-free: ``critic_api_key`` is not a field,
    so a payload supplying it is stripped and can never be exposed at rest."""
    profile = validate_agent_profile(
        {
            "name": "oh",
            "llm_profile_ref": "default",
            "verification": {
                "critic_enabled": True,
                "critic_model_name": "gpt-5.5",
                "critic_api_key": "sk-real-secret-value",
            },
        }
    )
    assert isinstance(profile, OpenHandsAgentProfile)
    assert not hasattr(profile.verification, "critic_api_key")
    assert profile.verification.critic_enabled is True
    assert profile.verification.critic_model_name == "gpt-5.5"

    # Even forcing secret exposure must not surface the value (it isn't stored).
    exposed = profile.model_dump(mode="json", context={"expose_secrets": True})
    assert "critic_api_key" not in exposed["verification"]
    assert "sk-real-secret-value" not in json.dumps(exposed)


def test_openhands_profile_has_no_embedded_skills_field() -> None:
    """Profiles no longer carry embedded ``skills`` (#4017): the field is gone,
    and ``extra="forbid"`` rejects a stray one rather than silently accepting
    or dropping it. This is what makes the profile genuinely secret-free at
    rest — the only field that could ever carry a secret (``skills[].mcp_tools``)
    is gone."""
    with pytest.raises(ValidationError):
        validate_agent_profile(
            {
                "agent_kind": "openhands",
                "name": "oh",
                "llm_profile_ref": "default",
                "schema_version": AGENT_PROFILE_SCHEMA_VERSION,
                "skills": [],
            }
        )


# ---------------------------------------------------------------------------
# clean v1 baseline: nothing ever shipped, so the removed ``skills`` /
# ``skill_refs`` fields are rejected (``extra="forbid"``), not migrated (#4017)
# ---------------------------------------------------------------------------


def test_removed_skills_field_is_rejected() -> None:
    """The embedded ``skills`` field never shipped, so a payload carrying it is a
    genuine ``extra="forbid"`` violation — there is no migration to drop it."""
    with pytest.raises(ValidationError):
        validate_agent_profile(
            {
                "schema_version": 1,
                "agent_kind": "openhands",
                "name": "oh",
                "llm_profile_ref": "default",
                "skills": [{"name": "old-skill", "content": "do stuff"}],
            }
        )


def test_removed_skill_refs_field_is_rejected() -> None:
    """The allow-list ``skill_refs`` was replaced by the ``disabled_skills``
    deny-list and never shipped, so a payload carrying it is rejected."""
    with pytest.raises(ValidationError):
        validate_agent_profile(
            {
                "schema_version": 1,
                "agent_kind": "openhands",
                "name": "oh",
                "llm_profile_ref": "default",
                "skill_refs": ["pdf-tools"],
            }
        )


def test_payload_without_disabled_skills_adopts_empty_default() -> None:
    """A payload that omits ``disabled_skills`` picks up the model default —
    ``[]`` (all discovered skills)."""
    profile = validate_agent_profile(
        {
            "schema_version": 1,
            "agent_kind": "openhands",
            "name": "oh",
            "llm_profile_ref": "default",
        }
    )
    assert isinstance(profile, OpenHandsAgentProfile)
    assert profile.disabled_skills == []
    assert profile.disabled_skills == []
