"""Tests for :func:`apply_agent_settings_diff` and the ``llm`` canonicalization.

``agent_settings`` is a discriminated union. Applying a sparse diff must treat
``agent_kind`` as a one-way narrowing gate: switching variants replaces the
base, same-variant edits deep-merge. The settings stores previously hand-rolled
this in several places; this exercises the single SDK owner.
"""

import pytest
from pydantic import SecretStr

from openhands.sdk import apply_agent_settings_diff, validate_agent_settings
from openhands.sdk.settings.model import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    ACPAgentSettings,
    LLMAgentSettings,
    OpenHandsAgentSettings,
)


# ── agent_kind is a narrowing gate, not a conversion knob ──────────────


def test_switch_openhands_to_acp_replaces_with_fresh_variant() -> None:
    base = {"agent_kind": "openhands", "llm": {"model": "gpt"}}

    result = apply_agent_settings_diff(
        base, {"agent_kind": "acp", "acp_server": "claude-code"}
    )

    assert isinstance(result, ACPAgentSettings)
    assert result.acp_server == "claude-code"
    # ACP's agent_context is nullable; the openhands llm.model is not carried.
    assert result.agent_context is None


def test_switch_to_acp_drops_persona() -> None:
    base = {"agent_kind": "openhands", "llm": {"model": "gpt"}, "persona": "x"}

    result = apply_agent_settings_diff(
        base, {"agent_kind": "acp", "acp_server": "claude-code"}
    )

    assert isinstance(result, ACPAgentSettings)
    assert "persona" not in result.model_dump()


def test_none_in_diff_clears_persona() -> None:
    base = {"agent_kind": "openhands", "llm": {"model": "gpt"}, "persona": "x"}

    result = apply_agent_settings_diff(base, {"persona": None})

    assert isinstance(result, OpenHandsAgentSettings)
    assert result.persona is None


def test_switch_acp_to_openhands_replaces_with_fresh_variant() -> None:
    base = {"agent_kind": "acp", "acp_server": "claude-code"}

    result = apply_agent_settings_diff(
        base, {"agent_kind": "openhands", "llm": {"model": "gpt"}}
    )

    assert isinstance(result, OpenHandsAgentSettings)
    assert result.agent_kind == "openhands"
    assert result.llm.model == "gpt"


def test_inline_fields_land_on_fresh_base_during_switch() -> None:
    base = {"agent_kind": "acp", "acp_server": "claude-code"}

    result = apply_agent_settings_diff(
        base, {"agent_kind": "openhands", "llm": {"model": "model-c"}}
    )

    assert isinstance(result, OpenHandsAgentSettings)
    assert result.llm.model == "model-c"


# ── the shared mcp_config catalog survives a kind switch ───────────────


def _server(name: str) -> dict:
    return {
        "transport": "http",
        "url": f"https://{name}.invalid/mcp",
        "headers": {"X-Api-Key": f"{name}-key"},
        "auth": {"strategy": "bearer", "value": f"{name}-token"},
    }


def _plain_catalog(settings: OpenHandsAgentSettings | ACPAgentSettings) -> dict:
    dumped = settings.model_dump(mode="json", context={"expose_secrets": True})
    return dumped["mcp_config"]


def _kind_switch_case(target: str) -> tuple[dict, dict]:
    """Return a non-empty base of the other variant and a switch to ``target``."""
    catalog = {"keep": _server("keep"), "other": _server("other")}
    if target == "acp":
        return (
            {"agent_kind": "openhands", "persona": "x", "mcp_config": catalog},
            {"agent_kind": "acp", "acp_server": "codex"},
        )
    return (
        {"agent_kind": "acp", "acp_server": "codex", "mcp_config": catalog},
        {"agent_kind": "openhands", "llm": {"model": "gpt"}},
    )


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_preserves_mcp_catalog_with_credentials(target: str) -> None:
    base, diff = _kind_switch_case(target)

    result = apply_agent_settings_diff(base, diff)

    assert result.agent_kind == target
    catalog = _plain_catalog(result)
    assert set(catalog) == {"keep", "other"}
    assert catalog["keep"]["url"] == "https://keep.invalid/mcp"
    assert catalog["keep"]["headers"] == {"X-Api-Key": "keep-key"}
    assert catalog["keep"]["auth"]["value"] == "keep-token"


def test_switch_with_mcp_catalog_still_resets_variant_fields() -> None:
    base, diff = _kind_switch_case("acp")
    to_acp = apply_agent_settings_diff(base, diff)
    assert isinstance(to_acp, ACPAgentSettings)
    assert "persona" not in to_acp.model_dump()
    assert to_acp.acp_server == "codex"

    base, diff = _kind_switch_case("openhands")
    to_openhands = apply_agent_settings_diff(base, diff)
    assert isinstance(to_openhands, OpenHandsAgentSettings)
    assert "acp_server" not in to_openhands.model_dump()
    assert to_openhands.llm.model == "gpt"


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_null_mcp_config_deletes_catalog(target: str) -> None:
    base, diff = _kind_switch_case(target)

    result = apply_agent_settings_diff(base, {**diff, "mcp_config": None})

    assert result.mcp_config == {}


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_null_server_entry_deletes_only_that_server(target: str) -> None:
    base, diff = _kind_switch_case(target)

    result = apply_agent_settings_diff(base, {**diff, "mcp_config": {"other": None}})

    assert set(result.mcp_config) == {"keep"}


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_sparse_server_patch_keeps_rest_of_server(target: str) -> None:
    base, diff = _kind_switch_case(target)

    result = apply_agent_settings_diff(
        base, {**diff, "mcp_config": {"keep": {"enabled": False}}}
    )

    catalog = _plain_catalog(result)
    assert set(catalog) == {"keep", "other"}
    assert catalog["keep"]["enabled"] is False
    assert catalog["keep"]["url"] == "https://keep.invalid/mcp"
    assert catalog["keep"]["headers"] == {"X-Api-Key": "keep-key"}
    assert catalog["keep"]["auth"]["value"] == "keep-token"


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_new_server_merges_into_catalog(target: str) -> None:
    base, diff = _kind_switch_case(target)

    result = apply_agent_settings_diff(
        base, {**diff, "mcp_config": {"new": _server("new")}}
    )

    assert set(result.mcp_config) == {"keep", "other", "new"}


def test_restated_kind_is_not_a_switch_for_mcp_config() -> None:
    base = {"agent_kind": "openhands", "mcp_config": {"keep": _server("keep")}}

    merged = apply_agent_settings_diff(
        base, {"agent_kind": "openhands", "mcp_config": {"new": _server("new")}}
    )
    cleared = apply_agent_settings_diff(base, {"mcp_config": None})

    assert set(merged.mcp_config) == {"keep", "new"}
    assert cleared.mcp_config == {}


# ── other server-wide state survives a kind switch ─────────────────────


def _server_wide_base(target: str, **fields) -> dict:
    """Return a base of the variant opposite ``target``, holding ``fields``."""
    if target == "acp":
        return {"agent_kind": "openhands", **fields}
    return {"agent_kind": "acp", "acp_server": "codex", **fields}


def _switch_to(target: str) -> dict:
    if target == "acp":
        return {"agent_kind": "acp", "acp_server": "codex"}
    return {"agent_kind": "openhands"}


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_preserves_load_memory_only(target: str) -> None:
    base = _server_wide_base(
        target,
        agent_context={"load_memory": True, "system_message_suffix": "stale"},
    )

    result = apply_agent_settings_diff(base, _switch_to(target))

    assert result.agent_context is not None
    assert result.agent_context.load_memory is True
    assert result.agent_context.system_message_suffix is None


def test_switch_without_load_memory_keeps_acp_context_null() -> None:
    base = {"agent_kind": "openhands", "agent_context": {"load_memory": False}}

    result = apply_agent_settings_diff(base, _switch_to("acp"))

    assert result.agent_context is None


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_applies_load_memory_patch(target: str) -> None:
    base = _server_wide_base(target, agent_context={"load_memory": True})

    turned_off = apply_agent_settings_diff(
        base, {**_switch_to(target), "agent_context": {"load_memory": False}}
    )
    reset = apply_agent_settings_diff(
        base, {**_switch_to(target), "agent_context": None}
    )

    assert turned_off.agent_context is not None
    assert turned_off.agent_context.load_memory is False
    assert reset.agent_context is None or reset.agent_context.load_memory is False


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_preserves_critic_api_key(target: str) -> None:
    base = _server_wide_base(target, verification={"critic_api_key": "critic-key"})

    result = apply_agent_settings_diff(base, _switch_to(target))

    dumped = result.model_dump(mode="json", context={"expose_secrets": True})
    assert dumped["verification"]["critic_api_key"] == "critic-key"


def test_switch_resets_critic_policy_but_not_its_key() -> None:
    base = {
        "agent_kind": "openhands",
        "verification": {
            "critic_enabled": True,
            "critic_server_url": "https://critic.invalid",
            "critic_api_key": "critic-key",
        },
    }

    round_trip = apply_agent_settings_diff(
        apply_agent_settings_diff(base, _switch_to("acp")), _switch_to("openhands")
    )

    assert isinstance(round_trip, OpenHandsAgentSettings)
    assert round_trip.verification.critic_enabled is False
    assert round_trip.verification.critic_server_url is None
    assert isinstance(round_trip.verification.critic_api_key, SecretStr)
    assert round_trip.verification.critic_api_key.get_secret_value() == "critic-key"


@pytest.mark.parametrize("target", ["acp", "openhands"])
def test_switch_applies_critic_api_key_patch(target: str) -> None:
    base = _server_wide_base(target, verification={"critic_api_key": "critic-key"})

    deleted = apply_agent_settings_diff(
        base, {**_switch_to(target), "verification": {"critic_api_key": None}}
    )
    replaced = apply_agent_settings_diff(
        base, {**_switch_to(target), "verification": {"critic_api_key": "new-key"}}
    )

    assert deleted.verification.critic_api_key is None
    dumped = replaced.model_dump(mode="json", context={"expose_secrets": True})
    assert dumped["verification"]["critic_api_key"] == "new-key"


def test_acp_settings_store_only_the_critic_key_from_a_verification_patch() -> None:
    base = {"agent_kind": "acp", "acp_server": "codex"}

    result = apply_agent_settings_diff(
        base,
        {"verification": {"critic_api_key": "critic-key", "critic_enabled": True}},
    )

    dumped = result.model_dump(mode="json", context={"expose_secrets": True})
    assert dumped["verification"] == {"critic_api_key": "critic-key"}


# ── same-variant deep merge ────────────────────────────────────────────


def test_same_kind_deep_merges_within_variant() -> None:
    base = {
        "agent_kind": "openhands",
        "llm": {"model": "gpt", "temperature": 0.5},
    }

    result = apply_agent_settings_diff(base, {"llm": {"temperature": 0.9}})

    assert isinstance(result, OpenHandsAgentSettings)
    # untouched nested key is preserved; only temperature changes
    assert result.llm.model == "gpt"
    assert result.llm.temperature == 0.9


def test_diff_without_agent_kind_keeps_base_kind() -> None:
    base = {"agent_kind": "acp", "acp_server": "claude-code"}

    result = apply_agent_settings_diff(base, {"acp_model": "claude-opus-4-6"})

    assert isinstance(result, ACPAgentSettings)
    assert result.acp_server == "claude-code"
    assert result.acp_model == "claude-opus-4-6"


def test_none_in_diff_unsets_key_merge_patch() -> None:
    base = {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "x"}

    result = apply_agent_settings_diff(base, {"acp_model": None})

    assert isinstance(result, ACPAgentSettings)
    assert result.acp_model is None


def test_empty_diff_returns_validated_base() -> None:
    base = {"agent_kind": "acp", "acp_server": "claude-code"}

    for diff in ({}, None):
        result = apply_agent_settings_diff(base, diff)
        assert isinstance(result, ACPAgentSettings)
        assert result.acp_server == "claude-code"


# ── base accepted as dict or instance; secrets survive ─────────────────


def test_base_may_be_a_settings_instance() -> None:
    base = OpenHandsAgentSettings.model_validate({"llm": {"model": "gpt"}})

    result = apply_agent_settings_diff(base, {"llm": {"model": "claude"}})

    assert isinstance(result, OpenHandsAgentSettings)
    assert result.llm.model == "claude"


def test_secret_in_base_survives_same_kind_merge() -> None:
    base = OpenHandsAgentSettings.model_validate(
        {"llm": {"model": "gpt", "api_key": "sk-SECRET"}}
    )

    # diff touches an unrelated field; the base api_key must not be masked away.
    result = apply_agent_settings_diff(base, {"llm": {"model": "claude"}})

    assert isinstance(result, OpenHandsAgentSettings)
    assert isinstance(result.llm.api_key, SecretStr)
    assert result.llm.api_key.get_secret_value() == "sk-SECRET"
    assert result.llm.model == "claude"


# ── llm tag canonicalization (closes the schema_version-gated gap) ──────


def test_validate_canonicalizes_llm_tag_at_current_schema_version() -> None:
    # The v1->v2 migration only renames 'llm' while advancing schema_version;
    # an 'llm' payload already at the current version must still canonicalize.
    result = validate_agent_settings(
        {
            "agent_kind": "llm",
            "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
            "llm": {"model": "legacy"},
        }
    )

    assert type(result) is OpenHandsAgentSettings
    assert result.agent_kind == "openhands"
    assert result.llm.model == "legacy"


def test_apply_diff_on_llm_tagged_base_returns_openhands() -> None:
    base = {
        "agent_kind": "llm",
        "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "llm": {"model": "legacy"},
    }

    result = apply_agent_settings_diff(base, {"llm": {"model": "new"}})

    assert type(result) is OpenHandsAgentSettings
    assert result.agent_kind == "openhands"
    assert result.llm.model == "new"


def test_validate_never_returns_llm_subclass() -> None:
    for version in range(0, AGENT_SETTINGS_SCHEMA_VERSION + 1):
        result = validate_agent_settings(
            {"agent_kind": "llm", "schema_version": version, "llm": {"model": "m"}}
        )
        assert not isinstance(result, LLMAgentSettings)
        assert result.agent_kind == "openhands"


# ── the deprecated class stays importable for back-compat ──────────────


def test_llm_agent_settings_remains_importable() -> None:
    from openhands.sdk.settings.model import LLMAgentSettings as _LLM

    assert issubclass(_LLM, OpenHandsAgentSettings)


@pytest.mark.parametrize("patch", [{}, {"persona": "updated"}])
def test_diff_loads_unversioned_persisted_openhands_base(patch) -> None:
    result = apply_agent_settings_diff(
        {"agent_kind": "openhands", "enable_sub_agents": True}, patch
    )
    assert isinstance(result, OpenHandsAgentSettings)
    if patch:
        assert result.persona == "updated"


@pytest.mark.parametrize("patch", [{}, {"acp_model": "sonnet"}])
def test_diff_loads_unversioned_persisted_acp_base(patch) -> None:
    result = apply_agent_settings_diff(
        {"agent_kind": "acp", "llm": {"model": "old"}}, patch
    )
    assert isinstance(result, ACPAgentSettings)
    assert result.acp_model == patch.get("acp_model")
    assert "llm" not in result.model_dump()
