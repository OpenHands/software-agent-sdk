import json
import shutil
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import (
    LLM,
    ACPAgentSettings,
    Agent,
    AgentContext,
    AgentSettingsBase,
    ConversationSettings,
    OpenHandsAgentSettings,
    SettingProminence,
    Tool,
    default_agent_settings,
    export_agent_settings_schema,
    validate_agent_settings,
)
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.context.condenser import LLMSummarizingCondenser, NoOpCondenser
from openhands.sdk.critic.base import IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config, dump_mcp_config
from openhands.sdk.secret import StaticSecret
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, ConfirmRisky
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    CondenserSettings,
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    VerificationSettings,
)
from openhands.sdk.settings.model import ACPServerKind
from openhands.sdk.workspace import LocalWorkspace


# Fields on LLM that have ``exclude=True`` and should not appear in the schema.
_LLM_EXCLUDED_FIELDS = {name for name, fi in LLM.model_fields.items() if fi.exclude}


# ---------------------------------------------------------------------------
# Schema export — per-variant
# ---------------------------------------------------------------------------


def test_llm_agent_settings_export_schema_groups_sections() -> None:
    schema = OpenHandsAgentSettings.export_schema()

    assert schema.model_name == "OpenHandsAgentSettings"
    section_keys = [section.key for section in schema.sections]
    assert section_keys == [
        "general",
        "llm",
        "condenser",
        "verification",
    ]

    sections = {s.key: s for s in schema.sections}

    # -- general section (top-level scalar fields) --
    general_fields = {f.key: f for f in sections["general"].fields}
    assert set(general_fields) == {
        "agent",
        "tools",
        "enable_sub_agents",
        "enable_switch_llm_tool",
        "tool_concurrency_limit",
        "mcp_config",
    }
    assert general_fields["agent"].default == "CodeActAgent"
    assert general_fields["agent"].prominence is SettingProminence.MAJOR
    assert general_fields["tools"].value_type == "array"
    # None = "server default toolset" (materialized by create_agent, #3978).
    assert general_fields["tools"].default is None
    assert general_fields["tools"].prominence is SettingProminence.MAJOR
    assert general_fields["enable_sub_agents"].value_type == "boolean"
    assert general_fields["enable_sub_agents"].default is False
    assert general_fields["enable_sub_agents"].prominence is SettingProminence.MAJOR
    assert general_fields["enable_switch_llm_tool"].value_type == "boolean"
    assert general_fields["enable_switch_llm_tool"].default is True
    assert (
        general_fields["enable_switch_llm_tool"].prominence is SettingProminence.MINOR
    )
    assert general_fields["tool_concurrency_limit"].value_type == "integer"
    assert general_fields["tool_concurrency_limit"].default == 1
    assert (
        general_fields["tool_concurrency_limit"].prominence is SettingProminence.MAJOR
    )

    # -- llm section --
    llm_fields = {f.key: f for f in sections["llm"].fields}
    expected_llm_keys = {
        f"llm.{name}" for name in LLM.model_fields if name not in _LLM_EXCLUDED_FIELDS
    }
    assert set(llm_fields) == expected_llm_keys

    assert llm_fields["llm.model"].value_type == "string"
    assert llm_fields["llm.model"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.max_input_tokens"].default is None
    assert llm_fields["llm.max_output_tokens"].default is None
    assert llm_fields["llm.api_key"].label == "API Key"
    assert llm_fields["llm.api_key"].secret is True
    assert llm_fields["llm.api_key"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.base_url"].prominence is SettingProminence.MAJOR

    # Excluded fields must not appear
    assert "llm.fallback_strategy" not in llm_fields
    assert "llm.retry_listener" not in llm_fields

    # -- condenser section --
    condenser_fields = {f.key: f for f in sections["condenser"].fields}
    assert (
        condenser_fields["condenser.enabled"].prominence is SettingProminence.CRITICAL
    )
    assert condenser_fields["condenser.condenser_kind"].default == "llm_summarizing"
    assert [
        choice.value for choice in condenser_fields["condenser.condenser_kind"].choices
    ] == ["llm_summarizing", "no_op"]
    assert condenser_fields["condenser.max_size"].depends_on == ["condenser.enabled"]
    assert condenser_fields["condenser.max_size"].prominence is SettingProminence.MINOR
    assert condenser_fields["condenser.max_tokens"].default is None
    assert condenser_fields["condenser.max_tokens"].depends_on == ["condenser.enabled"]
    assert (
        condenser_fields["condenser.max_tokens"].prominence is SettingProminence.MINOR
    )

    # -- verification section (critic settings only) --
    v_fields = {f.key: f for f in sections["verification"].fields}
    assert v_fields["verification.critic_mode"].value_type == "string"
    assert [c.value for c in v_fields["verification.critic_mode"].choices] == [
        "finish_and_message",
        "all_actions",
    ]
    assert (
        v_fields["verification.enable_iterative_refinement"].prominence
        is SettingProminence.CRITICAL
    )

    # The critic API key must surface in the schema as a CRITICAL, secret
    # field that depends on critic_enabled — this is what the GUI uses to
    # render a masked input gated on the toggle.
    critic_api_key = v_fields["verification.critic_api_key"]
    assert critic_api_key.secret is True
    assert critic_api_key.value_type == "string"
    assert critic_api_key.prominence is SettingProminence.CRITICAL
    assert critic_api_key.depends_on == ["verification.critic_enabled"]


def test_acp_agent_settings_export_schema_has_acp_section() -> None:
    schema = ACPAgentSettings.export_schema()
    assert schema.model_name == "ACPAgentSettings"

    section_keys = [section.key for section in schema.sections]
    assert "acp" in section_keys
    assert "llm" in section_keys  # kept for cost/pricing attribution

    sections = {s.key: s for s in schema.sections}
    acp_fields = {f.key: f for f in sections["acp"].fields}
    assert set(acp_fields) == {
        "acp_server",
        "acp_command",
        "acp_args",
        "acp_model",
        "acp_session_mode",
        "acp_prompt_timeout",
    }
    # Server picker + model are both critical — users pick server then
    # model. Raw command is a minor override for power users.
    assert acp_fields["acp_server"].prominence is SettingProminence.CRITICAL
    assert acp_fields["acp_model"].prominence is SettingProminence.CRITICAL
    assert acp_fields["acp_command"].prominence is SettingProminence.MINOR

    # mcp_config is exposed as a single object field (matching the OpenHands
    # variant) rather than being expanded into nested per-server fields. The
    # servers are forwarded to the ACP subprocess at session creation.
    general_fields = {f.key: f for f in sections["general"].fields}
    assert "mcp_config" in general_fields
    assert general_fields["mcp_config"].prominence is SettingProminence.MINOR


def test_conversation_settings_export_schema_groups_sections() -> None:
    schema = ConversationSettings.export_schema()

    assert schema.model_name == "ConversationSettings"
    section_keys = [section.key for section in schema.sections]
    assert section_keys == ["general", "verification"]

    sections = {s.key: s for s in schema.sections}
    general_fields = {f.key: f for f in sections["general"].fields}
    assert set(general_fields) == {"max_iterations"}
    assert general_fields["max_iterations"].default == 500
    assert general_fields["max_iterations"].prominence is SettingProminence.MAJOR

    verification_fields = {f.key: f for f in sections["verification"].fields}
    assert set(verification_fields) == {
        "confirmation_mode",
        "security_analyzer",
    }
    assert verification_fields["confirmation_mode"].default is False
    assert (
        verification_fields["confirmation_mode"].prominence
        is SettingProminence.CRITICAL
    )
    assert verification_fields["security_analyzer"].default == "llm"
    assert verification_fields["security_analyzer"].choices[0].value == "llm"
    assert verification_fields["security_analyzer"].depends_on == ["confirmation_mode"]


def test_conversation_settings_validates_observability_metadata() -> None:
    settings = ConversationSettings(observability_metadata={"repo": "OpenHands/sdk"})
    assert settings.observability_metadata == {"repo": "OpenHands/sdk"}

    with pytest.raises(ValidationError):
        ConversationSettings(observability_metadata={"": "missing-key"})

    with pytest.raises(ValidationError):
        ConversationSettings(observability_metadata=[])  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ConversationSettings(observability_tags=[1, 2])  # type: ignore[list-item]


def test_conversation_settings_model_dump_roundtrip() -> None:
    settings = ConversationSettings(
        max_iterations=42,
        confirmation_mode=True,
        security_analyzer="none",
    )

    restored = ConversationSettings.model_validate(settings.model_dump(mode="json"))

    assert restored == settings


def test_conversation_settings_create_request() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="llm",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, ConfirmRisky)
    assert isinstance(request.security_analyzer, LLMSecurityAnalyzer)

    overridden_request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
        max_iterations=5,
        confirmation_policy=AlwaysConfirm(),
        security_analyzer=None,
    )

    assert overridden_request.max_iterations == 5
    assert isinstance(overridden_request.confirmation_policy, AlwaysConfirm)
    assert overridden_request.security_analyzer is None


def test_conversation_settings_create_request_with_acp_agent() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="none",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = ACPAgent(acp_command=["echo", "test"])

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, AlwaysConfirm)
    assert request.security_analyzer is None


def test_acp_create_request_lifts_context_secrets_into_request_secrets() -> None:
    # End-to-end: provider creds supplied as agent_context.secrets (keyed by
    # the provider's env var name) are lifted into request.secrets by
    # create_request — the channel that lands them in state.secret_registry
    # on the agent-server, from where _start_acp_server injects them into the
    # subprocess env. llm.api_key is no longer read (deprecated, #3632).
    agent = ACPAgentSettings(
        acp_server="claude-code",
        agent_context=AgentContext(
            secrets={
                "ANTHROPIC_API_KEY": StaticSecret(value=SecretStr("sk-provider")),
                "GITHUB_TOKEN": StaticSecret(value=SecretStr("ghp_x")),
            }
        ),
    ).create_agent()

    request = ConversationSettings().create_request(
        StartConversationRequest,
        agent=agent,
        workspace=LocalWorkspace(working_dir="/tmp"),
    )

    assert set(request.secrets) == {"ANTHROPIC_API_KEY", "GITHUB_TOKEN"}
    assert request.secrets["ANTHROPIC_API_KEY"].get_value() == "sk-provider"
    assert request.secrets["GITHUB_TOKEN"].get_value() == "ghp_x"


# ---------------------------------------------------------------------------
# Schema export — combined (discriminated union)
# ---------------------------------------------------------------------------


def test_export_agent_settings_schema_emits_variant_tagged_sections() -> None:
    schema = export_agent_settings_schema()
    assert schema.model_name == "AgentSettings"

    by_keyvariant = {(s.key, s.variant): s for s in schema.sections}

    # Shared general section contains LLM-only top-level fields with
    # field-level variant="openhands" tags (so they hide on the ACP page).
    general = by_keyvariant.get(("general", None))
    assert general is not None
    general_keys = {f.key for f in general.fields}
    assert general_keys == {
        "agent",
        "tools",
        "enable_sub_agents",
        "enable_switch_llm_tool",
        "tool_concurrency_limit",
        "mcp_config",
    }
    # No agent_kind field — each variant has its own settings page and
    # injects the discriminator on save.
    assert "agent_kind" not in general_keys
    for f in general.fields:
        assert f.variant == "openhands", (
            f"expected field {f.key} variant=openhands, got {f.variant}"
        )

    # LLM-variant sections.
    assert ("llm", "openhands") in by_keyvariant
    assert ("condenser", "openhands") in by_keyvariant
    assert ("verification", "openhands") in by_keyvariant

    # ACP-variant sections.
    acp_section = by_keyvariant.get(("acp", "acp"))
    assert acp_section is not None
    acp_keys = {f.key for f in acp_section.fields}
    assert "acp_server" in acp_keys
    assert "acp_command" in acp_keys
    assert "acp_model" in acp_keys

    # acp_server is the critical user-visible field (the command is a
    # minor override).
    server_field = next(f for f in acp_section.fields if f.key == "acp_server")
    assert server_field.prominence is SettingProminence.CRITICAL
    server_choices = {c.value for c in server_field.choices}
    assert server_choices == {"claude-code", "codex", "gemini-cli", "custom"}

    command_field = next(f for f in acp_section.fields if f.key == "acp_command")
    assert command_field.prominence is SettingProminence.MINOR

    # ACP variant also has an LLM section (for cost/pricing attribution).
    assert ("llm", "acp") in by_keyvariant


# ---------------------------------------------------------------------------
# Discriminator + validation
# ---------------------------------------------------------------------------


def test_default_agent_settings_returns_openhands_variant() -> None:
    s = default_agent_settings()
    assert isinstance(s, OpenHandsAgentSettings)
    assert s.agent_kind == "openhands"


def test_validate_agent_settings_defaults_to_openhands_when_discriminator_missing() -> (
    None
):
    """Existing persisted payloads predate ``agent_kind`` — they must round-trip."""
    v = validate_agent_settings({"llm": {"model": "test-model"}})
    assert isinstance(v, OpenHandsAgentSettings)
    assert v.llm.model == "test-model"


def test_validate_agent_settings_dispatches_on_agent_kind() -> None:
    openhands = validate_agent_settings(
        {"agent_kind": "openhands", "llm": {"model": "m"}}
    )
    assert isinstance(openhands, OpenHandsAgentSettings)
    assert openhands.agent_kind == "openhands"

    legacy_llm = validate_agent_settings(
        {"agent_kind": "llm", "llm": {"model": "legacy-model"}}
    )
    assert isinstance(legacy_llm, OpenHandsAgentSettings)
    assert legacy_llm.agent_kind == "openhands"
    assert legacy_llm.llm.model == "legacy-model"

    acp = validate_agent_settings(
        {
            "agent_kind": "acp",
            "acp_command": ["npx", "-y", "claude-agent-acp"],
            "acp_model": "claude-opus-4-6",
        }
    )
    assert isinstance(acp, ACPAgentSettings)
    assert acp.acp_command == ["npx", "-y", "claude-agent-acp"]


def test_validate_agent_settings_migrates_v0_llm_payload() -> None:
    settings = validate_agent_settings({"llm": {"model": "test-model"}})

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert settings.agent_kind == "openhands"
    assert settings.llm.model == "test-model"


def test_validate_agent_settings_dispatches_current_acp_payload() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 1,
            "agent_kind": "acp",
            "acp_command": ["npx", "-y", "claude-agent-acp"],
            "acp_model": "claude-opus-4-6",
        }
    )

    assert isinstance(settings, ACPAgentSettings)
    # Migrations keep ACP payloads intact while bumping schema_version.
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert settings.acp_command == ["npx", "-y", "claude-agent-acp"]


def test_validate_agent_settings_canonicalizes_legacy_llm_kind() -> None:
    """v1 payloads with the deprecated ``agent_kind: 'llm'`` are migrated to
    the canonical ``'openhands'`` discriminator on read."""
    settings = validate_agent_settings(
        {
            "schema_version": 1,
            "agent_kind": "llm",
            "llm": {"model": "legacy-model"},
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert settings.agent_kind == "openhands"
    assert settings.llm.model == "legacy-model"


def test_validate_agent_settings_drops_legacy_verification_fields() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 2,
            "agent_kind": "openhands",
            "verification": {
                "critic_enabled": True,
                "confirmation_mode": True,
                "security_analyzer": "llm",
            },
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    verification = settings.verification.model_dump(mode="json")
    assert verification["critic_enabled"] is True
    assert "confirmation_mode" not in verification
    assert "security_analyzer" not in verification


def test_validate_agent_settings_migrates_legacy_openhands_proxy_llm() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 3,
            "agent_kind": "openhands",
            "llm": {
                "model": "litellm_proxy/claude-opus-4-8",
                "base_url": "https://llm-proxy.app.all-hands.dev/",
            },
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert settings.llm.model == "openhands/claude-opus-4-8"
    assert settings.llm.base_url is None


def test_validate_agent_settings_migrates_legacy_mcp_auth_shapes() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 4,
            "agent_kind": "openhands",
            "mcp_config": {
                "mcpServers": {
                    "oauth-top-level": {
                        "url": "https://mcp.oauth.example.com/mcp",
                        "unknown_secret": "drop-me",
                        "auth": "oauth",
                        "authentication": {
                            "type": "oauth",
                            "client_auth_method": "none",
                        },
                        "oauth_credentials": {
                            "mcp-oauth-token": {
                                "https://mcp.oauth.example.com/mcp/tokens": {
                                    "value": {
                                        "access_token": "access-secret",
                                        "refresh_token": "refresh-secret",
                                    }
                                }
                            },
                            "mcp-oauth-client-info": {
                                "https://mcp.oauth.example.com/mcp/client_info": {
                                    "value": {
                                        "client_id": "client-id",
                                        "client_secret": "client-secret",
                                    }
                                }
                            },
                            "mcp-oauth-token-expiry": {
                                "https://mcp.oauth.example.com/mcp/token_expiry": {
                                    "value": {"expires_at": 1234.5},
                                    "expires_at": 9999.0,
                                }
                            },
                        },
                    },
                    "oauth-auth-field": {
                        "url": "https://mcp.auth.example.com/mcp",
                        "auth": {
                            "strategy": "oauth2",
                            "credentials": {
                                "mcp-oauth-token": {
                                    "https://mcp.auth.example.com/mcp/tokens": {
                                        "value": {"access_token": "field-secret"}
                                    }
                                }
                            },
                        },
                    },
                    "fastmcp-fields": {
                        "url": "https://mcp.fields.example.com/mcp",
                        "type": "streamable-http",
                        "description": "Fields server",
                        "icon": "https://example.com/icon.png",
                        "timeout": 10,
                        "sse_read_timeout": 20,
                        "keep_alive": True,
                        "unknown_secret": "drop-me",
                    },
                    "bearer": {
                        "url": "https://mcp.bearer.example.com/mcp",
                        "auth": "bearer-secret",
                    },
                    "api-key": {
                        "url": "https://mcp.api.example.com/mcp",
                        "api_key": "api-secret",
                    },
                    "authorization": {
                        "url": "https://mcp.header.example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer header-secret",
                            "X-Other": "other",
                        },
                    },
                    "sse": {
                        "url": "https://mcp.linear.app/sse",
                        "transport": "sse",
                        "auth": {"strategy": "bearer", "value": "linear-secret"},
                    },
                }
            },
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    servers = dump_mcp_config(settings.mcp_config)

    assert servers["oauth-top-level"]["auth"] == {
        "strategy": "oauth2",
        "authentication": {"type": "oauth", "client_auth_method": "none"},
        "state": {
            "tokens": {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
            },
            "client_info": {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            "token_expires_at": 1234.5,
        },
    }
    assert "oauth_credentials" not in servers["oauth-top-level"]
    assert "authentication" not in servers["oauth-top-level"]
    assert "unknown_secret" not in servers["oauth-top-level"]

    assert servers["fastmcp-fields"] == {
        "url": "https://mcp.fields.example.com/mcp",
        "transport": "streamable-http",
        "description": "Fields server",
        "icon": "https://example.com/icon.png",
        "timeout": 10.0,
        "sse_read_timeout": 20.0,
        "keep_alive": True,
    }

    assert servers["oauth-auth-field"]["auth"] == {
        "strategy": "oauth2",
        "state": {"tokens": {"access_token": "field-secret"}},
    }
    assert servers["bearer"]["auth"] == {
        "strategy": "bearer",
        "value": "bearer-secret",
    }
    assert servers["api-key"]["auth"] == {
        "strategy": "api_key",
        "value": "api-secret",
    }
    assert servers["authorization"]["auth"] == {
        "strategy": "bearer",
        "value": "header-secret",
    }
    assert servers["authorization"]["headers"] == {"X-Other": "other"}
    assert "sse" not in servers
    assert servers["shttp"] == {
        "url": "https://mcp.linear.app/mcp",
        "auth": {"strategy": "bearer", "value": "linear-secret"},
    }


def test_validate_agent_settings_migrates_mcp_type_to_transport() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 4,
            "agent_kind": "openhands",
            "mcp_config": {
                "http": {
                    "url": "https://mcp.example.com/mcp",
                    "type": "streamable-http",
                },
                "shttp-alias": {
                    "url": "https://mcp.alias.example.com/mcp",
                    "type": "shttp",
                },
            },
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert dump_mcp_config(settings.mcp_config) == {
        "http": {
            "url": "https://mcp.example.com/mcp",
            "transport": "streamable-http",
        },
        "shttp-alias": {
            "url": "https://mcp.alias.example.com/mcp",
            "transport": "http",
        },
    }


def test_openhands_mcp_config_reject_unknown_server_fields() -> None:
    with pytest.raises(ValidationError):
        OpenHandsAgentSettings.model_validate(
            {
                "mcp_config": {
                    "server": {
                        "url": "https://mcp.example.com/mcp",
                        "unknown_secret": "not-a-datamodel-field",
                    }
                }
            }
        )


def test_validate_agent_settings_mcp_linear_migration_does_not_duplicate() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 4,
            "agent_kind": "openhands",
            "mcp_config": {
                "mcpServers": {
                    "sse": {
                        "url": "https://mcp.linear.app/sse",
                        "transport": "sse",
                        "auth": {"strategy": "bearer", "value": "old-secret"},
                    },
                    "shttp": {
                        "url": "https://mcp.linear.app/mcp",
                        "auth": {"strategy": "bearer", "value": "manual-secret"},
                    },
                }
            },
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert dump_mcp_config(settings.mcp_config) == {
        "shttp": {
            "url": "https://mcp.linear.app/mcp",
            "auth": {"strategy": "bearer", "value": "manual-secret"},
        }
    }


def test_validate_agent_settings_rejects_newer_schema_version() -> None:
    with pytest.raises(
        ValueError,
        match=f"newer than supported version {AGENT_SETTINGS_SCHEMA_VERSION}",
    ):
        validate_agent_settings(
            {"schema_version": AGENT_SETTINGS_SCHEMA_VERSION + 1, "llm": {"model": "m"}}
        )


def test_openhands_agent_settings_from_persisted_migrates_legacy_payloads() -> None:
    v0 = OpenHandsAgentSettings.from_persisted({"llm": {"model": "v0-model"}})
    assert isinstance(v0, OpenHandsAgentSettings)
    assert v0.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert v0.agent_kind == "openhands"
    assert v0.llm.model == "v0-model"

    v1 = OpenHandsAgentSettings.from_persisted(
        {
            "schema_version": 1,
            "agent_kind": "llm",
            "llm": {"model": "legacy-model"},
        }
    )
    assert isinstance(v1, OpenHandsAgentSettings)
    assert v1.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert v1.agent_kind == "openhands"
    assert v1.llm.model == "legacy-model"

    v2 = OpenHandsAgentSettings.from_persisted(
        {
            "schema_version": 2,
            "agent_kind": "openhands",
            "verification": {
                "critic_enabled": True,
                "confirmation_mode": True,
                "security_analyzer": "llm",
            },
        }
    )
    assert isinstance(v2, OpenHandsAgentSettings)
    assert v2.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert v2.verification.critic_enabled is True
    verification = v2.verification.model_dump(mode="json")
    assert "confirmation_mode" not in verification
    assert "security_analyzer" not in verification


def test_acp_agent_settings_from_persisted_returns_acp_subtype() -> None:
    settings = ACPAgentSettings.from_persisted(
        {
            "schema_version": 1,
            "agent_kind": "acp",
            "acp_command": ["echo", "test"],
            "acp_model": "claude-opus-4-6",
        }
    )

    assert type(settings) is ACPAgentSettings
    assert settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert settings.acp_command == ["echo", "test"]


def test_openhands_agent_settings_from_persisted_rejects_current_llm_kind() -> None:
    with pytest.raises(ValidationError):
        OpenHandsAgentSettings.from_persisted(
            {
                "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
                "agent_kind": "llm",
                "llm": {"model": "legacy-model"},
            }
        )


def test_agent_settings_from_persisted_current_payload_matches_model_validate() -> None:
    payload = OpenHandsAgentSettings(llm=LLM(model="current-model")).model_dump(
        mode="json"
    )
    original_payload = json.loads(json.dumps(payload))

    settings = OpenHandsAgentSettings.from_persisted(payload)

    assert settings == OpenHandsAgentSettings.model_validate(payload)
    assert payload == original_payload


def test_openhands_agent_settings_from_persisted_matches_union_validator() -> None:
    payload = {
        "schema_version": 1,
        "agent_kind": "llm",
        "llm": {"model": "legacy-model"},
    }

    from_persisted_settings = OpenHandsAgentSettings.from_persisted(payload)
    union_settings = validate_agent_settings(payload)

    # agent_context.current_datetime defaults to now(); exclude it so the
    # comparison is not sensitive to the microseconds between validations.
    volatile = {"agent_context": {"current_datetime"}}
    assert from_persisted_settings.model_dump(
        exclude=volatile
    ) == union_settings.model_dump(exclude=volatile)


def test_agent_settings_from_persisted_rejects_malformed_payload() -> None:
    with pytest.raises(ValidationError):
        OpenHandsAgentSettings.from_persisted(
            {
                "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
                "agent_kind": "openhands",
                "llm": "not-an-llm-settings-payload",
            }
        )


def test_conversation_settings_from_persisted_migrates_v0_payload() -> None:
    settings = ConversationSettings.from_persisted({"max_iterations": 42})

    assert settings.schema_version == 1
    assert settings.max_iterations == 42


def test_conversation_settings_from_persisted_rejects_newer_schema_version() -> None:
    with pytest.raises(ValueError, match="newer than supported version 1"):
        ConversationSettings.from_persisted({"schema_version": 2})


# ---------------------------------------------------------------------------
# create_agent — LLM variant
# ---------------------------------------------------------------------------


def test_llm_create_agent_uses_settings_llm_and_tools() -> None:
    llm = LLM(model="test-model")
    tools = [Tool(name="TerminalTool")]
    settings = OpenHandsAgentSettings(llm=llm, tools=tools)
    agent = settings.create_agent()
    assert isinstance(agent, Agent)
    assert agent.llm is llm
    assert agent.tools == tools


def test_llm_create_agent_defaults_tool_concurrency_limit_to_one() -> None:
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()
    assert agent.tool_concurrency_limit == 1


def test_create_agent_defaults_tools_when_none() -> None:
    """tools=None (the default) materializes the canonical exec set at
    create_agent — the single defaulting point (#3967 / #3978). Deterministic:
    browser is a serving-layer injection, never part of the default."""
    settings = OpenHandsAgentSettings(llm=LLM(model="test-model"))
    assert settings.tools is None
    agent = settings.create_agent()
    assert [t.name for t in agent.tools] == ["terminal", "file_editor", "task_tracker"]


def test_create_agent_default_tools_honor_enable_sub_agents() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="test-model"), enable_sub_agents=True
    )
    agent = settings.create_agent()
    assert [t.name for t in agent.tools] == [
        "terminal",
        "file_editor",
        "task_tracker",
        "task_tool_set",
    ]


def test_create_agent_empty_tools_stays_bare() -> None:
    """tools=[] is an explicit choice: no default injection (persisted-payload
    compatibility — [] predates the None default and keeps its old meaning)."""
    settings = OpenHandsAgentSettings(llm=LLM(model="test-model"), tools=[])
    agent = settings.create_agent()
    assert agent.tools == []


def test_tool_concurrency_limit_defaults_to_one_when_omitted_from_payload() -> None:
    # Backward compatibility: payloads persisted before the field existed must
    # still load and fall back to the sequential default.
    settings = OpenHandsAgentSettings.model_validate({"agent_kind": "openhands"})
    assert settings.tool_concurrency_limit == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, 1),  # sequential default, explicit
        (2, 2),
        (8, 8),
        (32, 32),
        (1000, 1000),  # no upper cap — large values are accepted
        ("4", 4),  # lax string -> int coercion
        (4.0, 4),  # whole-number float coercion
        (True, 1),  # bool is an int subclass; True -> 1 (>= 1, so valid)
    ],
)
def test_tool_concurrency_limit_valid_values_round_trip(
    raw: Any, expected: int
) -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="test-model"), tool_concurrency_limit=raw
    )
    assert settings.tool_concurrency_limit == expected
    assert type(settings.tool_concurrency_limit) is int

    # The value must survive a JSON serialization round-trip...
    reloaded = OpenHandsAgentSettings.model_validate(settings.model_dump(mode="json"))
    assert reloaded.tool_concurrency_limit == expected

    # ...and propagate to the constructed Agent.
    agent = settings.create_agent()
    assert agent.tool_concurrency_limit == expected


@pytest.mark.parametrize(
    "raw",
    [
        0,  # below ge=1
        -1,  # negative
        -100,
        False,  # bool False -> 0, below ge=1
        4.5,  # non-integral float
        "abc",  # unparseable string
        None,  # not Optional
        [1],  # wrong type entirely
    ],
)
def test_tool_concurrency_limit_invalid_values_rejected(raw: Any) -> None:
    with pytest.raises(ValidationError) as exc_info:
        OpenHandsAgentSettings(llm=LLM(model="test-model"), tool_concurrency_limit=raw)
    assert any(
        err["loc"] == ("tool_concurrency_limit",) for err in exc_info.value.errors()
    )


def test_llm_agent_settings_validates_mcp_config_as_typed_model() -> None:
    settings = OpenHandsAgentSettings.model_validate(
        {"mcp_config": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
    )

    assert isinstance(settings.mcp_config["fetch"], MCPServer)
    assert settings.model_dump()["mcp_config"] == {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
    }


def test_llm_create_agent_serializes_typed_mcp_config_compactly() -> None:
    mcp_config = coerce_mcp_config(
        {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)

    agent = settings.create_agent()

    assert dump_mcp_config(agent.mcp_config) == {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
    }


def test_llm_create_agent_builds_condenser_when_enabled() -> None:
    llm = LLM(model="test-model", usage_id="agent")
    agent_metrics = llm.metrics
    settings = OpenHandsAgentSettings(
        llm=llm,
        condenser=LLMSummarizingCondenserSettings(
            enabled=True,
            max_size=100,
            max_tokens=5000,
            keep_first=3,
            minimum_progress=0.2,
            hard_context_reset_max_retries=7,
            hard_context_reset_context_scaling=0.6,
        ),
    )
    agent = settings.create_agent()

    assert agent.llm is llm
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    assert agent.condenser.max_size == 100
    assert agent.condenser.max_tokens == 5000
    assert agent.condenser.keep_first == 3
    assert agent.condenser.minimum_progress == 0.2
    assert agent.condenser.hard_context_reset_max_retries == 7
    assert agent.condenser.hard_context_reset_context_scaling == 0.6
    assert agent.condenser.llm is not llm
    assert agent.condenser.llm.model == llm.model
    assert agent.condenser.llm.usage_id == "condenser"
    assert agent.condenser.llm.metrics is not agent_metrics


def test_llm_summarizing_condenser_settings_match_condenser_fields() -> None:
    condenser_fields = set(LLMSummarizingCondenser.model_fields) - {"llm"}
    settings_fields = set(LLMSummarizingCondenserSettings.model_fields) - {
        "enabled",
        "condenser_kind",
    }

    assert settings_fields == condenser_fields


def test_openhands_agent_settings_defaults_legacy_condenser_payload() -> None:
    settings = OpenHandsAgentSettings.model_validate(
        {
            "condenser": {
                "enabled": True,
                "max_size": 100,
                "max_tokens": 5000,
            }
        }
    )

    assert isinstance(settings.condenser, LLMSummarizingCondenserSettings)
    assert settings.condenser.condenser_kind == "llm_summarizing"
    assert settings.condenser.max_size == 100
    assert settings.condenser.max_tokens == 5000


def test_openhands_agent_settings_dispatches_no_op_condenser_payload() -> None:
    settings = OpenHandsAgentSettings.model_validate(
        {
            "condenser": {
                "enabled": True,
                "condenser_kind": "no_op",
            }
        }
    )

    assert isinstance(settings.condenser, NoOpCondenserSettings)
    assert settings.condenser.condenser_kind == "no_op"
    assert settings.condenser.model_dump() == {
        "enabled": True,
        "condenser_kind": "no_op",
    }


def test_openhands_agent_settings_upgrades_base_condenser_settings_instance() -> None:
    settings = OpenHandsAgentSettings.model_validate(
        {"condenser": CondenserSettings(enabled=True, max_size=100)}
    )

    assert isinstance(settings.condenser, LLMSummarizingCondenserSettings)
    assert settings.condenser.max_size == 100


def test_condenser_settings_base_requires_concrete_build_method() -> None:
    with pytest.raises(NotImplementedError):
        CondenserSettings().build_condenser(LLM(model="test-model"))


def test_llm_create_agent_no_condenser_when_disabled() -> None:
    settings = OpenHandsAgentSettings(
        condenser=LLMSummarizingCondenserSettings(enabled=False),
    )
    agent = settings.create_agent()
    assert agent.condenser is None


def test_llm_create_agent_builds_no_op_condenser_variant() -> None:
    settings = OpenHandsAgentSettings(condenser=NoOpCondenserSettings())

    agent = settings.create_agent()

    assert isinstance(agent.condenser, NoOpCondenser)


def test_llm_create_agent_builds_critic_when_enabled() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=SecretStr("k")),
        verification=VerificationSettings(
            critic_enabled=True,
            critic_mode="all_actions",
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    assert agent.critic.mode == "all_actions"
    assert agent.critic.iterative_refinement is None


def test_llm_create_agent_no_critic_without_api_key() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=None),
        verification=VerificationSettings(critic_enabled=True),
    )
    agent = settings.create_agent()
    assert agent.critic is None


def test_llm_create_agent_critic_uses_explicit_api_key() -> None:
    """When ``verification.critic_api_key`` is set, the critic authenticates
    with it instead of the LLM key. The LLM's own key is preserved untouched
    so the main agent loop still talks to its provider."""
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=SecretStr("llm-key")),
        verification=VerificationSettings(
            critic_enabled=True,
            critic_api_key=SecretStr("critic-key"),
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    assert isinstance(agent.critic.api_key, SecretStr)
    assert agent.critic.api_key.get_secret_value() == "critic-key"
    # LLM key unaffected.
    assert isinstance(agent.llm.api_key, SecretStr)
    assert agent.llm.api_key.get_secret_value() == "llm-key"


def test_llm_create_agent_critic_falls_back_to_llm_api_key() -> None:
    """Without ``verification.critic_api_key``, the legacy behavior holds:
    the critic reuses the LLM key (auto-config path for the All-Hands proxy)."""
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=SecretStr("llm-key")),
        verification=VerificationSettings(critic_enabled=True),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    assert isinstance(agent.critic.api_key, SecretStr)
    assert agent.critic.api_key.get_secret_value() == "llm-key"


def test_llm_create_agent_critic_with_only_critic_api_key() -> None:
    """If the LLM has no key but ``critic_api_key`` is supplied, the critic
    is still built — its credential is independent of the LLM's."""
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=None),
        verification=VerificationSettings(
            critic_enabled=True,
            critic_api_key=SecretStr("critic-only-key"),
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    assert isinstance(agent.critic.api_key, SecretStr)
    assert agent.critic.api_key.get_secret_value() == "critic-only-key"


def test_verification_settings_critic_api_key_roundtrip() -> None:
    """``critic_api_key`` survives dump → validate when secrets are exposed,
    and validates from both plain strings and SecretStr inputs."""
    settings = VerificationSettings(
        critic_enabled=True,
        critic_api_key="plain-string-key",
    )
    assert isinstance(settings.critic_api_key, SecretStr)
    assert settings.critic_api_key.get_secret_value() == "plain-string-key"

    dumped = settings.model_dump(context={"expose_secrets": "plaintext"})
    assert dumped["critic_api_key"] == "plain-string-key"

    restored = VerificationSettings.model_validate(dumped)
    assert isinstance(restored.critic_api_key, SecretStr)
    assert restored.critic_api_key.get_secret_value() == "plain-string-key"

    # Empty strings normalize to None (consistent with LLM.api_key handling).
    empty = VerificationSettings(critic_enabled=True, critic_api_key="")
    assert empty.critic_api_key is None


def test_llm_create_agent_critic_with_iterative_refinement() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=SecretStr("k")),
        verification=VerificationSettings(
            critic_enabled=True,
            enable_iterative_refinement=True,
            critic_threshold=0.8,
            max_refinement_iterations=5,
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    ir = agent.critic.iterative_refinement
    assert isinstance(ir, IterativeRefinementConfig)
    assert ir.success_threshold == 0.8
    assert ir.max_iterations == 5


def test_llm_roundtrip_preserves_llm_model() -> None:
    settings = OpenHandsAgentSettings(llm=LLM(model="test-model"))
    data = settings.model_dump()
    restored = OpenHandsAgentSettings.model_validate(data)
    assert restored.llm.model == "test-model"


# ---------------------------------------------------------------------------
# create_agent — ACP variant
# ---------------------------------------------------------------------------


def test_acp_create_agent_uses_server_default_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``acp_server`` set but no explicit command, use the built-in default.

    Pin ``shutil.which`` to ``None`` so the ``npx`` default is asserted
    deterministically — on a host where the pinned ``claude-agent-acp`` binary
    is installed, :meth:`resolve_acp_command` would (correctly) rewrite to it.
    """
    monkeypatch.setattr(shutil, "which", lambda _: None)
    settings = ACPAgentSettings(acp_server="claude-code", acp_model="claude-opus-4-6")
    agent = settings.create_agent()
    assert isinstance(agent, ACPAgent)
    assert agent.acp_command == [
        "npx",
        "-y",
        "@agentclientprotocol/claude-agent-acp@0.44.0",
    ]
    assert agent.acp_model == "claude-opus-4-6"
    # The authoritative provider key is carried onto the agent.
    assert agent.acp_server == "claude-code"


def test_acp_create_agent_carries_provider_key() -> None:
    """``create_agent`` stamps the provider key onto the agent for every choice.

    ``acp_command`` does not reliably reverse-map to a provider, so the key must
    ride the agent (and thus ``ConversationInfo.agent``) for consumers to resolve
    a brand label / model list. Custom carries through verbatim; an agent built
    directly (not from settings) defaults to ``None``; and the key survives a
    serialization round-trip through the ``AgentBase`` discriminated union.
    """
    for server in ("claude-code", "codex", "gemini-cli", "custom"):
        kwargs: dict[str, Any] = {"acp_server": server}
        if server == "custom":
            kwargs["acp_command"] = ["my-acp"]
        agent = ACPAgentSettings(**kwargs).create_agent()
        assert agent.acp_server == server

    assert ACPAgent(acp_command=["x"]).acp_server is None

    agent = ACPAgentSettings(acp_server="gemini-cli").create_agent()
    reloaded = ACPAgent.model_validate_json(agent.model_dump_json())
    assert reloaded.acp_server == "gemini-cli"


def test_acp_resolve_command_for_known_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every non-custom choice must map to a runnable default.

    With no pinned binary on ``PATH`` (``shutil.which`` → ``None``), the
    default stays the ``npx`` invocation.
    """
    monkeypatch.setattr(shutil, "which", lambda _: None)
    for server in ("claude-code", "codex", "gemini-cli"):
        settings = ACPAgentSettings(acp_server=server)
        cmd = settings.resolve_acp_command()
        assert cmd, f"expected default command for {server}, got empty"
        assert cmd[0] == "npx", f"expected npx-based default, got {cmd}"


def test_acp_create_agent_explicit_command_overrides_default() -> None:
    settings = ACPAgentSettings(
        acp_server="claude-code",
        acp_command=["my-local-acp-binary"],
    )
    agent = settings.create_agent()
    assert agent.acp_command == ["my-local-acp-binary"]


def test_acp_custom_server_requires_explicit_command() -> None:
    settings = ACPAgentSettings(acp_server="custom")
    try:
        settings.create_agent()
    except ValueError as e:
        assert "acp_command" in str(e) and "custom" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_acp_create_agent_forwards_isolate_data_dir() -> None:
    """``acp_isolate_data_dir`` propagates from settings to the built agent.

    Off by default, and an explicit True reaches ``ACPAgent`` so a deploying
    application can opt conversations sharing one sandbox into a per-conversation
    CLI data dir (#1019).
    """
    default_agent = ACPAgentSettings(acp_server="codex").create_agent()
    assert default_agent.acp_isolate_data_dir is False

    isolated = ACPAgentSettings(
        acp_server="codex", acp_isolate_data_dir=True
    ).create_agent()
    assert isolated.acp_isolate_data_dir is True


def test_acp_custom_server_with_command_resolves() -> None:
    settings = ACPAgentSettings(
        acp_server="custom",
        acp_command=["bin", "--flag"],
    )
    assert settings.resolve_acp_command() == ["bin", "--flag"]


# ---------------------------------------------------------------------------
# resolve_acp_command() — prefer the pinned, pre-installed CLI binary
#
# The agent-server image pre-installs the ACP CLIs and exposes them as wrappers
# on PATH (claude-agent-acp / codex-acp / gemini). resolve_acp_command rewrites
# the ``npx -y <pkg>`` launch command — the registry default AND the explicit
# acp_command canvas sends — to run the pinned binary directly when it is on
# PATH (reproducible, no runtime npm download), preserving trailing args. When
# the binary is absent (local dev), it falls back to the npx command unchanged.
# ---------------------------------------------------------------------------


def _which_returning(*available: str):
    """Build a ``shutil.which`` stub resolving only the named binaries."""
    paths = {name: f"/usr/local/bin/{name}" for name in available}
    return lambda name: paths.get(name)


@pytest.mark.parametrize(
    ("server", "binary", "expected"),
    [
        ("claude-code", "claude-agent-acp", ["claude-agent-acp"]),
        ("codex", "codex-acp", ["codex-acp"]),
        # gemini's default carries a trailing ``--acp`` that must be preserved.
        ("gemini-cli", "gemini", ["gemini", "--acp"]),
    ],
)
def test_acp_resolve_command_rewrites_default_to_pinned_binary(
    monkeypatch: pytest.MonkeyPatch,
    server: ACPServerKind,
    binary: str,
    expected: list[str],
) -> None:
    """(a) Registry default + binary on PATH → run the pinned binary directly."""
    monkeypatch.setattr(shutil, "which", _which_returning(binary))
    settings = ACPAgentSettings(acp_server=server)
    assert settings.resolve_acp_command() == expected


def test_acp_resolve_command_rewrites_explicit_npx_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) Explicit ``npx -y <pkg>`` (what canvas sends) + binary on PATH →
    rewritten to the pinned binary, with trailing args preserved."""
    monkeypatch.setattr(shutil, "which", _which_returning("codex-acp"))
    settings = ACPAgentSettings(
        acp_server="codex",
        acp_command=[
            "npx",
            "-y",
            "@agentclientprotocol/codex-acp",
            "--verbose",
        ],
    )
    assert settings.resolve_acp_command() == ["codex-acp", "--verbose"]


def test_acp_resolve_command_rewrites_versioned_npx_to_pinned_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b') Matching is version-agnostic: an ``npx`` command for the provider's
    package at *any* version (bare, the pinned default, or a drifted pin a client
    sends) rewrites to the pinned binary on PATH — in the image we always stand
    the reviewed binary in for the provider's package."""
    monkeypatch.setattr(shutil, "which", _which_returning("codex-acp"))
    for pkg in (
        "@agentclientprotocol/codex-acp",
        "@agentclientprotocol/codex-acp@1.1.2",
    ):
        settings = ACPAgentSettings(
            acp_server="codex",
            acp_command=["npx", "-y", pkg],
        )
        assert settings.resolve_acp_command() == ["codex-acp"], pkg


def test_acp_resolve_command_keeps_npx_when_binary_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) Binary not on PATH (local dev) → the ``npx`` command is unchanged.

    The default is version-pinned, so the native fallback launches the reviewed
    version rather than npm ``latest``.
    """
    monkeypatch.setattr(shutil, "which", lambda _: None)
    settings = ACPAgentSettings(acp_server="codex")
    assert settings.resolve_acp_command() == [
        "npx",
        "-y",
        "@agentclientprotocol/codex-acp@1.1.2",
    ]


def test_acp_resolve_command_leaves_custom_binary_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) A non-npx / user-supplied command is never rewritten, even when the
    provider's pinned binary is on PATH."""
    monkeypatch.setattr(shutil, "which", _which_returning("codex-acp"))
    settings = ACPAgentSettings(
        acp_server="codex",
        acp_command=["/opt/my-codex", "--flag"],
    )
    assert settings.resolve_acp_command() == ["/opt/my-codex", "--flag"]


def test_acp_resolve_command_leaves_unknown_package_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) An ``npx`` command for a *different* package is not rewritten — the
    pinned binary only stands in for the provider's own package."""
    monkeypatch.setattr(shutil, "which", _which_returning("codex-acp"))
    settings = ACPAgentSettings(
        acp_server="codex",
        acp_command=["npx", "-y", "@other/some-acp"],
    )
    assert settings.resolve_acp_command() == ["npx", "-y", "@other/some-acp"]


def test_acp_resolve_command_custom_server_never_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``custom`` has no registry entry (hence no pinned binary), so even a
    command that looks exactly like codex's is returned verbatim."""
    monkeypatch.setattr(shutil, "which", _which_returning("codex-acp", "gemini"))
    settings = ACPAgentSettings(
        acp_server="custom",
        acp_command=["npx", "-y", "@agentclientprotocol/codex-acp"],
    )
    assert settings.resolve_acp_command() == [
        "npx",
        "-y",
        "@agentclientprotocol/codex-acp",
    ]


def test_acp_resolve_command_queries_which_with_binary_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PATH probe uses the provider's ``binary_name``, not the npm package."""
    queried: list[str] = []

    def fake_which(name: str) -> str | None:
        queried.append(name)
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    ACPAgentSettings(acp_server="gemini-cli").resolve_acp_command()
    assert queried == ["gemini"]


def test_acp_create_agent_uses_pinned_binary_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: create_agent() bakes the rewritten command into the agent."""
    monkeypatch.setattr(shutil, "which", _which_returning("codex-acp"))
    agent = ACPAgentSettings(acp_server="codex").create_agent()
    assert agent.acp_command == ["codex-acp"]


def test_acp_api_key_env_var_maps_known_servers() -> None:
    assert (
        ACPAgentSettings(acp_server="claude-code").api_key_env_var
        == "ANTHROPIC_API_KEY"
    )
    assert ACPAgentSettings(acp_server="codex").api_key_env_var == "OPENAI_API_KEY"
    assert ACPAgentSettings(acp_server="gemini-cli").api_key_env_var == "GEMINI_API_KEY"
    assert (
        ACPAgentSettings(acp_server="custom", acp_command=["x"]).api_key_env_var is None
    )


def test_acp_create_agent_ignores_llm_credentials() -> None:
    # llm.api_key/base_url are never read: an LLM-profile base_url would leak
    # into the subprocess and silently re-route its API calls (#3632). Provider
    # credentials ride the conversation secrets channel keyed by the provider's
    # env var name; the caller's secrets pass through untouched.
    context = AgentContext(secrets={"GITHUB_TOKEN": "ghp_test"})
    settings = ACPAgentSettings(
        acp_server="codex",
        llm=LLM(
            model="gpt-5.4",
            api_key=SecretStr("sk-openai"),
            base_url="https://llm-proxy.example.com",
        ),
        agent_context=context,
    )

    agent = settings.create_agent()

    # The caller's secrets pass through untouched; no provider creds appear.
    assert agent.agent_context is not None
    assert dict(agent.agent_context.secrets or {}) == {"GITHUB_TOKEN": "ghp_test"}


def test_acp_create_agent_ignores_credentials_without_context() -> None:
    # No caller agent_context: nothing is synthesized for the ignored creds —
    # the context stays None.
    settings = ACPAgentSettings(
        acp_server="claude-code",
        llm=LLM(model="claude-opus-4-6", api_key=SecretStr("sk-ui-key")),
    )

    agent = settings.create_agent()

    assert agent.agent_context is None


def test_acp_create_agent_passes_caller_context_through() -> None:
    # Secrets supplied via agent_context reach the agent unchanged; they are
    # seeded into state.secret_registry at conversation init (the canonical
    # channel), not rewritten here.
    context = AgentContext(secrets={"ANTHROPIC_API_KEY": "sk-direct"})
    settings = ACPAgentSettings(acp_server="claude-code", agent_context=context)

    agent = settings.create_agent()

    assert agent.agent_context is context


def test_llm_agent_settings_public_alias_removed() -> None:
    """The deprecated ``LLMAgentSettings`` public import aliases were removed in
    v1.24.0; the class itself is retained (internal-only) for the union."""
    import openhands.sdk as _sdk_mod
    import openhands.sdk.settings as _settings_mod

    with pytest.raises(AttributeError):
        getattr(_settings_mod, "LLMAgentSettings")
    with pytest.raises(AttributeError):
        getattr(_sdk_mod, "LLMAgentSettings")

    # The class is still reachable at its canonical internal location and keeps
    # agent_kind="llm" so the discriminated union deserializes legacy payloads
    # and the API-breakage checker sees no field-value change.
    from openhands.sdk.settings.model import LLMAgentSettings

    assert issubclass(LLMAgentSettings, OpenHandsAgentSettings)
    settings = LLMAgentSettings(llm=LLM(model="test-model"))
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_kind == "llm"
    assert settings.llm.model == "test-model"


# ---------------------------------------------------------------------------
# ConversationSettings.create_request — dispatches on variant
# ---------------------------------------------------------------------------


def test_conversation_settings_create_request_for_llm_variant() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="llm",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, ConfirmRisky)
    assert isinstance(request.security_analyzer, LLMSecurityAnalyzer)


def test_conversation_settings_create_request_with_acp_agent_variant() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="none",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = ACPAgentSettings(acp_command=["echo", "test"]).create_agent()

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, AlwaysConfirm)
    assert request.security_analyzer is None


def test_conversation_settings_agent_settings_field_accepts_both_variants() -> None:
    """The agent_settings runtime field should accept either variant."""
    llm_conv = ConversationSettings(
        agent_settings=OpenHandsAgentSettings(llm=LLM(model="m")),
    )
    assert isinstance(llm_conv.agent_settings, OpenHandsAgentSettings)

    acp_conv = ConversationSettings(
        agent_settings=ACPAgentSettings(acp_command=["x"]),
    )
    assert isinstance(acp_conv.agent_settings, ACPAgentSettings)


# ---------------------------------------------------------------------------
# Secret redaction in settings serialization
# ---------------------------------------------------------------------------


def test_openhands_agent_settings_mcp_config_redacts_env_and_headers() -> None:
    mcp_config = coerce_mcp_config(
        {
            "leaky": {
                "command": "echo",
                "args": ["mcp"],
                "env": {"API_KEY": "sk-mcp-secret"},
                "headers": {"X-API-Token": "tok-mcp-secret"},
            }
        }
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)

    blob = settings.model_dump_json()
    assert "sk-mcp-secret" not in blob
    assert "tok-mcp-secret" not in blob

    exposed = settings.model_dump(context={"expose_secrets": True})
    leaky = exposed["mcp_config"]["leaky"]
    assert leaky["env"]["API_KEY"] == "sk-mcp-secret"
    assert leaky["headers"]["X-API-Token"] == "tok-mcp-secret"


def test_mcp_config_encrypts_env_and_headers_with_cipher() -> None:
    """When a cipher is in the serialization context (the on-disk persistence
    path), MCP ``env`` / ``headers`` values must be encrypted per-value with
    that cipher — the same way other secret fields are persisted.

    Round-tripping through ``model_validate`` with the same cipher must
    recover the original plaintext values.
    """
    from openhands.sdk.utils.cipher import Cipher

    mcp_config = coerce_mcp_config(
        {
            "github": {
                "command": "uvx",
                "args": ["mcp-server-github"],
                "env": {"GITHUB_TOKEN": "ghp-mcp-secret"},
            },
            "fetch": {
                "url": "https://example.com/mcp",
                "headers": {"X-API-Token": "tok-mcp-secret"},
            },
        }
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)
    cipher = Cipher(secret_key="test-encryption-key")

    dumped = settings.model_dump(mode="json", context={"cipher": cipher})

    servers = dumped["mcp_config"]
    assert isinstance(servers, dict)
    github = servers["github"]
    fetch = servers["fetch"]
    assert isinstance(github, dict)
    assert isinstance(fetch, dict)
    github_env = github["env"]
    fetch_headers = fetch["headers"]
    assert isinstance(github_env, dict)
    assert isinstance(fetch_headers, dict)
    enc_token = github_env["GITHUB_TOKEN"]
    enc_auth = fetch_headers["X-API-Token"]
    assert isinstance(enc_token, str)
    assert isinstance(enc_auth, str)

    # Plaintext values must NOT appear on disk.
    serialized = json.dumps(dumped)
    assert "ghp-mcp-secret" not in serialized
    assert "tok-mcp-secret" not in serialized
    assert "**********" not in serialized

    # Values must be Fernet ciphertext (base64; starts with "gAAAA").
    assert enc_token.startswith("gAAAA")
    assert enc_auth.startswith("gAAAA")
    # Non-secret structure must remain plaintext.
    assert servers["github"]["command"] == "uvx"
    assert servers["github"]["args"] == ["mcp-server-github"]
    assert servers["fetch"]["url"] == "https://example.com/mcp"

    # Round-trip: decrypt with the same cipher recovers the originals.
    restored = OpenHandsAgentSettings.model_validate(dumped, context={"cipher": cipher})
    restored_dump = dump_mcp_config(restored.mcp_config)
    restored_github_env = restored_dump["github"]["env"]
    restored_fetch_headers = restored_dump["fetch"]["headers"]
    assert isinstance(restored_github_env, dict)
    assert isinstance(restored_fetch_headers, dict)
    assert restored_github_env["GITHUB_TOKEN"] == "ghp-mcp-secret"
    assert restored_fetch_headers["X-API-Token"] == "tok-mcp-secret"


def test_mcp_config_encrypts_bearer_auth_with_cipher() -> None:
    from openhands.sdk.utils.cipher import Cipher

    mcp_config = coerce_mcp_config(
        {
            "linear": {
                "url": "https://mcp.linear.app/mcp",
                "auth": {"strategy": "bearer", "value": "lin-api-secret"},
            },
            "superhuman": {
                "url": "https://mcp.mail.superhuman.com/mcp",
                "auth": {"strategy": "oauth2"},
            },
        }
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)
    cipher = Cipher(secret_key="test-encryption-key")

    dumped = settings.model_dump(mode="json", context={"cipher": cipher})
    servers = dumped["mcp_config"]

    assert servers["linear"]["auth"]["strategy"] == "bearer"
    assert servers["linear"]["auth"]["value"].startswith("gAAAA")
    assert servers["linear"]["auth"]["value"] != "lin-api-secret"
    assert servers["superhuman"]["auth"] == {"strategy": "oauth2"}

    redacted = settings.model_dump(mode="json")
    assert redacted["mcp_config"]["linear"]["auth"] == {
        "strategy": "bearer",
        "value": "**********",
    }
    assert redacted["mcp_config"]["superhuman"]["auth"] == {"strategy": "oauth2"}

    restored = OpenHandsAgentSettings.model_validate(dumped, context={"cipher": cipher})
    restored_servers = dump_mcp_config(restored.mcp_config)
    assert restored_servers["linear"]["auth"] == {
        "strategy": "bearer",
        "value": "lin-api-secret",
    }
    assert restored_servers["superhuman"]["auth"] == {
        "strategy": "oauth2",
    }


def test_openhands_agent_settings_create_agent_preserves_mcp_auth_model() -> None:
    mcp_config = coerce_mcp_config(
        {
            "linear": {
                "url": "https://mcp.linear.app/mcp",
                "auth": {"strategy": "bearer", "value": "lin-api-secret"},
            },
            "superhuman": {
                "url": "https://mcp.mail.superhuman.com/mcp",
                "auth": {
                    "strategy": "oauth2",
                    "authentication": {
                        "type": "oauth",
                        "client_auth_method": "none",
                    },
                },
            },
        }
    )

    agent = OpenHandsAgentSettings(mcp_config=mcp_config).create_agent()

    servers = dump_mcp_config(agent.mcp_config)
    assert servers["linear"]["auth"] == {
        "strategy": "bearer",
        "value": "lin-api-secret",
    }
    assert servers["superhuman"]["auth"] == {
        "strategy": "oauth2",
        "authentication": {
            "type": "oauth",
            "client_auth_method": "none",
        },
    }


def test_openhands_agent_settings_mcp_config_decrypt_legacy_plaintext_on_disk() -> None:
    """Loading a settings file that pre-dates per-value encryption (env /
    headers stored as plaintext) must NOT drop those values: each value that
    isn't a valid Fernet token is passed through unchanged so the next save
    can re-encrypt it.
    """
    from openhands.sdk.utils.cipher import Cipher

    cipher = Cipher(secret_key="test-encryption-key")
    legacy_payload = {
        "mcp_config": {
            "github": {
                "command": "uvx",
                "args": ["mcp-server-github"],
                # plaintext, as the previous (pre-encryption) build wrote
                "env": {"GITHUB_TOKEN": "ghp-legacy-plaintext"},
            }
        }
    }

    restored = OpenHandsAgentSettings.model_validate(
        legacy_payload, context={"cipher": cipher}
    )
    restored_env = dump_mcp_config(restored.mcp_config)["github"]["env"]
    assert isinstance(restored_env, dict)
    assert restored_env["GITHUB_TOKEN"] == "ghp-legacy-plaintext"


def test_openhands_agent_settings_mcp_config_expose_encrypted_requires_cipher() -> None:
    """``expose_secrets="encrypted"`` without a cipher must raise — mirroring
    the contract used for individual ``SecretStr`` fields via
    :func:`serialize_secret`. Pydantic wraps the inner
    ``MissingCipherError`` in a ``PydanticSerializationError``; the
    agent-server's ``translate_missing_cipher`` walks the cause chain to
    surface a 503.
    """
    from pydantic_core import PydanticSerializationError

    from openhands.sdk.utils.pydantic_secrets import MissingCipherError

    settings = OpenHandsAgentSettings(
        mcp_config=coerce_mcp_config(
            {
                "github": {
                    "command": "uvx",
                    "args": ["mcp-server-github"],
                    "env": {"GITHUB_TOKEN": "ghp-secret"},
                }
            }
        )
    )
    with pytest.raises(PydanticSerializationError) as exc_info:
        settings.model_dump(mode="json", context={"expose_secrets": "encrypted"})
    assert MissingCipherError.__name__ in str(exc_info.value)


def test_openhands_agent_settings_mcp_config_expose_plaintext_passes_through() -> None:
    """``expose_secrets="plaintext"`` must return raw env / headers values
    even when a cipher is also in the context (e.g. an admin GET with
    explicit plaintext exposure).
    """
    from openhands.sdk.utils.cipher import Cipher

    settings = OpenHandsAgentSettings(
        mcp_config=coerce_mcp_config(
            {
                "github": {
                    "command": "uvx",
                    "args": ["mcp-server-github"],
                    "env": {"GITHUB_TOKEN": "ghp-secret"},
                }
            }
        )
    )
    cipher = Cipher(secret_key="test-encryption-key")

    dumped = settings.model_dump(
        mode="json",
        context={"cipher": cipher, "expose_secrets": "plaintext"},
    )
    assert dumped["mcp_config"]["github"]["env"]["GITHUB_TOKEN"] == "ghp-secret"


def test_openhands_agent_settings_create_agent_keeps_real_mcp_secrets() -> None:
    # create_agent must hand the runtime real env/headers (the field serializer
    # redacts mcp_config for transit only).
    mcp_config = coerce_mcp_config(
        {
            "leaky": {
                "command": "echo",
                "args": ["mcp"],
                "env": {"API_KEY": "sk-mcp-secret"},
            }
        }
    )
    agent = OpenHandsAgentSettings(mcp_config=mcp_config).create_agent()

    env = dump_mcp_config(agent.mcp_config)["leaky"]["env"]
    assert isinstance(env, dict)
    assert env["API_KEY"] == "sk-mcp-secret"


def test_acp_agent_settings_mcp_config_redacts_env_and_headers() -> None:
    mcp_config = coerce_mcp_config(
        {
            "leaky": {
                "command": "echo",
                "args": ["mcp"],
                "env": {"API_KEY": "sk-mcp-secret"},
                "headers": {"X-API-Token": "tok-mcp-secret"},
            }
        }
    )
    settings = ACPAgentSettings(acp_command=["echo"], mcp_config=mcp_config)

    blob = settings.model_dump_json()
    assert "sk-mcp-secret" not in blob
    assert "tok-mcp-secret" not in blob

    exposed = settings.model_dump(context={"expose_secrets": True})
    leaky = exposed["mcp_config"]["leaky"]
    env = leaky["env"]
    headers = leaky["headers"]
    assert isinstance(env, dict)
    assert isinstance(headers, dict)
    assert env["API_KEY"] == "sk-mcp-secret"
    assert headers["X-API-Token"] == "tok-mcp-secret"


def test_acp_agent_settings_create_agent_keeps_real_mcp_secrets() -> None:
    # create_agent must hand the subprocess real env/headers (the field serializer
    # redacts mcp_config for transit/storage only).
    mcp_config = coerce_mcp_config(
        {
            "leaky": {
                "command": "echo",
                "args": ["mcp"],
                "env": {"API_KEY": "sk-mcp-secret"},
                "headers": {"X-API-Token": "tok-mcp-secret"},
            }
        }
    )
    agent = ACPAgentSettings(acp_command=["echo"], mcp_config=mcp_config).create_agent()

    assert isinstance(agent, ACPAgent)
    dumped = dump_mcp_config(agent.mcp_config)["leaky"]
    env = dumped["env"]
    headers = dumped["headers"]
    assert isinstance(env, dict)
    assert isinstance(headers, dict)
    assert env["API_KEY"] == "sk-mcp-secret"
    assert headers["X-API-Token"] == "tok-mcp-secret"


# ---------------------------------------------------------------------------
# AgentSettingsBase — shared interface
# ---------------------------------------------------------------------------


def test_agent_settings_base_is_parent_of_both_variants() -> None:
    assert issubclass(OpenHandsAgentSettings, AgentSettingsBase)
    assert issubclass(ACPAgentSettings, AgentSettingsBase)


def test_agent_settings_base_schema_version_inherited() -> None:
    openhands = OpenHandsAgentSettings()
    acp = ACPAgentSettings(acp_command=["x"])
    assert openhands.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert acp.schema_version == AGENT_SETTINGS_SCHEMA_VERSION


def test_agent_settings_base_export_schema_works_on_both_variants() -> None:
    openhands_schema = OpenHandsAgentSettings.export_schema()
    acp_schema = ACPAgentSettings.export_schema()
    assert openhands_schema.model_name == "OpenHandsAgentSettings"
    assert acp_schema.model_name == "ACPAgentSettings"


def test_agent_settings_base_create_agent_is_callable_via_interface() -> None:
    """Both variants expose create_agent() through the shared base type."""
    settings: AgentSettingsBase = OpenHandsAgentSettings(llm=LLM(model="test-model"))
    agent = settings.create_agent()
    assert isinstance(agent, Agent)

    acp_settings: AgentSettingsBase = ACPAgentSettings(acp_command=["x"])
    from openhands.sdk.agent.acp_agent import ACPAgent

    acp_agent = acp_settings.create_agent()
    assert isinstance(acp_agent, ACPAgent)


# ---------------------------------------------------------------------------
# ACPAgentSettings — provider registry integration
# ---------------------------------------------------------------------------


def test_acp_settings_provider_info_returns_registry_entry() -> None:
    settings = ACPAgentSettings(acp_server="claude-code")
    info = settings.provider_info
    assert info is not None
    assert info.key == "claude-code"
    assert info.display_name == "Claude Code"


def test_acp_settings_provider_info_returns_none_for_custom() -> None:
    settings = ACPAgentSettings(acp_server="custom", acp_command=["x"])
    assert settings.provider_info is None


def test_acp_settings_api_key_env_var_from_registry() -> None:
    assert (
        ACPAgentSettings(acp_server="claude-code").api_key_env_var
        == "ANTHROPIC_API_KEY"
    )
    assert ACPAgentSettings(acp_server="codex").api_key_env_var == "OPENAI_API_KEY"
    assert ACPAgentSettings(acp_server="gemini-cli").api_key_env_var == "GEMINI_API_KEY"
    assert (
        ACPAgentSettings(acp_server="custom", acp_command=["x"]).api_key_env_var is None
    )


def test_acp_settings_base_url_env_var_from_registry() -> None:
    assert (
        ACPAgentSettings(acp_server="claude-code").base_url_env_var
        == "ANTHROPIC_BASE_URL"
    )
    assert ACPAgentSettings(acp_server="codex").base_url_env_var == "OPENAI_BASE_URL"
    assert (
        ACPAgentSettings(acp_server="gemini-cli").base_url_env_var == "GEMINI_BASE_URL"
    )
    assert (
        ACPAgentSettings(acp_server="custom", acp_command=["x"]).base_url_env_var
        is None
    )


def test_acp_resolve_command_uses_registry_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.settings.acp_providers import ACP_PROVIDERS

    # No pinned binary on PATH → registry npx default is returned verbatim.
    monkeypatch.setattr(shutil, "which", lambda _: None)
    for server_key in ("claude-code", "codex", "gemini-cli"):
        settings = ACPAgentSettings(acp_server=server_key)
        expected = list(ACP_PROVIDERS[server_key].default_command)
        assert settings.resolve_acp_command() == expected


# ---------------------------------------------------------------------------
# Agent capability helpers
# ---------------------------------------------------------------------------


def test_regular_agent_supports_all_capabilities() -> None:
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()
    assert agent.supports_openhands_tools is True
    assert agent.supports_condenser is True
    assert agent.agent_kind == "openhands"


def test_acp_agent_reports_no_openhands_capabilities() -> None:
    from openhands.sdk.agent.acp_agent import ACPAgent

    agent = ACPAgent(acp_command=["x"])
    assert agent.supports_openhands_tools is False
    assert agent.supports_condenser is False
    assert agent.agent_kind == "acp"


def test_llm_subscription_fields_roundtrip() -> None:
    settings = validate_agent_settings(
        {
            "llm": {
                "model": "gpt-5.6",
                "auth_type": "subscription",
                "subscription_vendor": "openai",
            }
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.llm.auth_type == "subscription"
    assert settings.llm.subscription_vendor == "openai"
    dumped = settings.model_dump(mode="json", exclude_none=True)
    assert dumped["llm"]["auth_type"] == "subscription"
    assert dumped["llm"]["subscription_vendor"] == "openai"
    assert "api_key" not in dumped["llm"]


def test_llm_create_agent_resolves_subscription_llm(monkeypatch) -> None:
    from openhands.sdk.llm.auth import openai

    original_llm = LLM(
        model="gpt-5.6",
        auth_type="subscription",
        subscription_vendor="openai",
    )
    runtime_llm = LLM(model="openai/gpt-5.6")
    runtime_llm._is_subscription = True

    def fake_create_subscription_llm_from_config(llm: LLM) -> LLM:
        assert llm is original_llm
        return runtime_llm

    monkeypatch.setattr(
        openai,
        "create_subscription_llm_from_config",
        fake_create_subscription_llm_from_config,
    )

    agent = OpenHandsAgentSettings(llm=original_llm).create_agent()

    assert agent.llm is runtime_llm
    assert agent.llm.is_subscription is True


def test_llm_from_persisted_rehydrates_subscription_runtime(monkeypatch) -> None:
    from openhands.sdk.llm.auth import openai

    runtime_llm = LLM(model="openai/gpt-5.6", auth_type="subscription")
    runtime_llm._is_subscription = True

    def fake_create_subscription_llm_from_config(llm: LLM) -> LLM:
        assert llm.auth_type == "subscription"
        assert llm.subscription_vendor == "openai"
        return runtime_llm

    monkeypatch.setattr(
        openai,
        "create_subscription_llm_from_config",
        fake_create_subscription_llm_from_config,
    )

    loaded = LLM.from_persisted(
        {
            "model": "gpt-5.6",
            "auth_type": "subscription",
            "subscription_vendor": "openai",
            "schema_version": 1,
        }
    )

    assert loaded is runtime_llm
    assert loaded.is_subscription is True


def test_llm_load_from_env_rehydrates_subscription_runtime(monkeypatch) -> None:
    from openhands.sdk.llm.auth import openai

    runtime_llm = LLM(model="openai/gpt-5.6", auth_type="subscription")
    runtime_llm._is_subscription = True

    def fake_create_subscription_llm_from_config(llm: LLM) -> LLM:
        assert llm.auth_type == "subscription"
        assert llm.subscription_vendor == "openai"
        assert llm.model == "gpt-5.6"
        return runtime_llm

    monkeypatch.setenv("LLM_MODEL", "gpt-5.6")
    monkeypatch.setenv("LLM_AUTH_TYPE", "subscription")
    monkeypatch.setenv("LLM_SUBSCRIPTION_VENDOR", "openai")
    monkeypatch.setattr(
        openai,
        "create_subscription_llm_from_config",
        fake_create_subscription_llm_from_config,
    )

    loaded = LLM.load_from_env()

    assert loaded is runtime_llm
    assert loaded.is_subscription is True


def test_create_subscription_llm_from_config_preserves_runtime_llm(monkeypatch) -> None:
    import openhands.sdk.llm.auth.openai as openai_auth

    class UnexpectedAuth:
        def __init__(self, *args, **kwargs):
            raise AssertionError("runtime subscription LLM should not be rehydrated")

    monkeypatch.setattr(openai_auth, "OpenAISubscriptionAuth", UnexpectedAuth)
    runtime_llm = LLM(
        model="openai/gpt-5.6",
        auth_type="subscription",
        subscription_vendor="openai",
    )
    runtime_llm._is_subscription = True

    assert openai_auth.create_subscription_llm_from_config(runtime_llm) is runtime_llm


@pytest.mark.asyncio
async def test_async_subscription_api_key_uses_async_refresh(monkeypatch) -> None:
    import openhands.sdk.llm.auth.openai as openai_auth
    from openhands.sdk.llm.auth.credentials import OAuthCredentials

    credentials = OAuthCredentials(
        vendor="openai",
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=4_102_444_800_000,
    )

    class FakeAuth:
        def __init__(self, credential_store=None):
            self.credential_store = credential_store

        async def refresh_if_needed(self):
            return credentials

        def refresh_if_needed_sync(self):
            raise AssertionError("async transport must not sync-refresh credentials")

        def extract_chatgpt_account_id(self, refreshed_credentials):
            assert refreshed_credentials is credentials
            return "account-id"

    monkeypatch.setattr(openai_auth, "OpenAISubscriptionAuth", FakeAuth)
    llm = LLM(
        model="openai/gpt-5.6",
        auth_type="subscription",
        subscription_vendor="openai",
    )
    llm._is_subscription = True

    api_key, extra_headers = await llm._aget_litellm_auth_values()
    assert api_key == "access-token"
    assert extra_headers["chatgpt-account-id"] == "account-id"
    assert llm.extra_headers is None


def test_sync_subscription_api_key_uses_valid_runtime_credentials(
    monkeypatch,
) -> None:
    import openhands.sdk.llm.auth.openai as openai_auth
    from openhands.sdk.llm.auth.credentials import OAuthCredentials

    credentials = OAuthCredentials(
        vendor="openai",
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=4_102_444_800_000,
    )

    class FakeAuth:
        def __init__(self, credential_store=None):
            self.credential_store = credential_store

        def refresh_if_needed_sync(self):
            raise AssertionError("valid runtime credentials should not sync-refresh")

        def extract_chatgpt_account_id(self, refreshed_credentials):
            assert refreshed_credentials is credentials
            return "account-id"

    monkeypatch.setattr(openai_auth, "OpenAISubscriptionAuth", FakeAuth)
    llm = LLM(
        model="openai/gpt-5.6",
        auth_type="subscription",
        subscription_vendor="openai",
    )
    llm._is_subscription = True
    llm._subscription_credentials = credentials

    api_key, extra_headers = llm._get_litellm_auth_values()
    assert api_key == "access-token"
    assert extra_headers["chatgpt-account-id"] == "account-id"
    assert llm.extra_headers is None


def test_openai_subscription_create_llm_serializes_subscription_auth(
    monkeypatch,
) -> None:
    import openhands.sdk.llm.auth.openai as openai_auth
    from openhands.sdk.llm.auth.credentials import OAuthCredentials
    from openhands.sdk.llm.auth.openai import OpenAISubscriptionAuth

    monkeypatch.setattr(openai_auth, "_extract_chatgpt_account_id", lambda _: None)

    llm = OpenAISubscriptionAuth().create_llm(
        model="gpt-5.6",
        credentials=OAuthCredentials(
            vendor="openai",
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=4_102_444_800_000,
        ),
        usage_id="profile:test",
    )

    assert llm.auth_type == "subscription"
    assert llm.subscription_vendor == "openai"
    assert llm.api_key is None
    assert llm.to_persisted()["auth_type"] == "subscription"
    assert llm.to_persisted()["subscription_vendor"] == "openai"
    assert "api_key" not in llm.to_persisted(context={"expose_secrets": "plaintext"})
    assert "base_url" not in llm.to_persisted()


def test_create_subscription_llm_from_config_preserves_non_auth_options(
    monkeypatch,
) -> None:
    import openhands.sdk.llm.auth.openai as openai_auth
    from openhands.sdk.llm.auth.credentials import OAuthCredentials

    captured: dict[str, object] = {}

    class FakeAuth:
        def refresh_if_needed_sync(self):
            return OAuthCredentials(
                vendor="openai",
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=4_102_444_800_000,
            )

        def create_llm(self, **kwargs):
            captured.update(kwargs)
            return LLM(
                model=f"openai/{kwargs['model']}",
                auth_type="subscription",
                subscription_vendor="openai",
                usage_id=kwargs["usage_id"],
                timeout=kwargs["timeout"],
            )

    monkeypatch.setattr(openai_auth, "OpenAISubscriptionAuth", FakeAuth)
    source = LLM(
        model="gpt-5.6",
        auth_type="subscription",
        subscription_vendor="openai",
        usage_id="profile:test",
        timeout=123,
    )

    runtime = openai_auth.create_subscription_llm_from_config(source)

    assert runtime.usage_id == "profile:test"
    assert runtime.timeout == 123
    assert captured["usage_id"] == "profile:test"
    assert captured["timeout"] == 123
    assert "api_key" not in captured
    assert "base_url" not in captured
