"""Tests for :func:`apply_agent_settings_diff` and the ``llm`` canonicalization.

``agent_settings`` is a discriminated union. Applying a sparse diff must treat
``agent_kind`` as a one-way narrowing gate: switching variants replaces the
base, same-variant edits deep-merge. The settings stores previously hand-rolled
this in several places; this exercises the single SDK owner.
"""

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
    base = OpenHandsAgentSettings(llm={"model": "gpt"})

    result = apply_agent_settings_diff(base, {"llm": {"model": "claude"}})

    assert isinstance(result, OpenHandsAgentSettings)
    assert result.llm.model == "claude"


def test_secret_in_base_survives_same_kind_merge() -> None:
    base = OpenHandsAgentSettings(llm={"model": "gpt", "api_key": "sk-SECRET"})

    # diff touches an unrelated field; the base api_key must not be masked away.
    result = apply_agent_settings_diff(base, {"llm": {"model": "claude"}})

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
