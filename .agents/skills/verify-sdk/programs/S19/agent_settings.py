"""Describe an agent as data, the way a settings UI or a saved config does:
build it, save it as JSON, reload it, migrate old payloads, apply a UI diff,
export the schema, and run a scripted conversation with the built agent.

Each section reports facts; a section that raises reports `<name>_error`.
"""

import json
import warnings

from pydantic import SecretStr
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Conversation, OpenHandsAgentSettings, Tool
from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    CONVERSATION_SETTINGS_SCHEMA_VERSION,
    ACPAgentSettings,
    ConversationSettings,
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    VerificationSettings,
    apply_agent_settings_diff,
    export_agent_settings_schema,
    export_settings_schema,
    validate_agent_settings,
)
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


out: dict = {"ok": True}


def section(name):
    def wrap(fn):
        try:
            out[name] = fn()
        except Exception as exc:  # report, let the recipe judge
            out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return fn

    return wrap


def llm(**kw) -> LLM:
    return LLM(
        model="openai/settings-model",
        api_key=SecretStr("sk-llm"),
        usage_id="agent",
        **kw,
    )


def plain(value):
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def tool_names(agent) -> list[str]:
    return sorted(
        {t.name for t in agent.tools} | set(agent.include_default_tools or [])
    )


MCP = {
    "github": {
        "command": "uvx",
        "args": ["mcp-server-github"],
        "env": {"GITHUB_TOKEN": "ghp-secret"},
    },
    "remote": {
        "url": "https://example.com/mcp",
        "headers": {"X-API-Token": "tok-secret"},
    },
    "off": {"command": "uvx", "args": ["mcp-server-fetch"], "enabled": False},
}


@section("create_agent")
def _():
    from openhands.sdk import AgentContext

    s = OpenHandsAgentSettings(
        llm=llm(),
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        mcp_config=MCP,
        agent_context=AgentContext(system_message_suffix="Answer tersely."),
    )
    a = s.create_agent()
    return {
        "type": type(a).__name__,
        "model": a.llm.model,
        "tools": [t.name for t in a.tools],
        "mcp_servers": sorted(a.mcp_config),
        "suffix": a.agent_context.system_message_suffix if a.agent_context else None,
    }


@section("roundtrip")
def _():
    s = OpenHandsAgentSettings(
        llm=llm(),
        tools=[Tool(name=TerminalTool.name)],
        condenser=LLMSummarizingCondenserSettings(
            enabled=True, max_size=50, keep_first=3
        ),
        mcp_config=MCP,
    )
    payload = json.loads(json.dumps(s.model_dump(mode="json")))
    back = OpenHandsAgentSettings.model_validate(payload)
    persisted = OpenHandsAgentSettings.from_persisted(payload)
    return {
        "schema_version": payload.get("schema_version"),
        "model": back.llm.model,
        "condenser_max_size": back.condenser.max_size,
        "condenser_keep_first": back.condenser.keep_first,
        "tools": [t.name for t in back.tools or []],
        "off_server_enabled": back.mcp_config["off"].enabled,
        "off_server_command": back.mcp_config["off"].command,
        "llm_key_in_plain_dump": "sk-llm" in json.dumps(payload),
        "from_persisted_equal": persisted.model_dump(mode="json")
        == back.model_dump(mode="json"),
    }


@section("tools")
def _():
    default = OpenHandsAgentSettings(llm=llm(), tools=None).create_agent()
    bare = OpenHandsAgentSettings(llm=llm(), tools=[]).create_agent()
    dup = OpenHandsAgentSettings(
        llm=llm(),
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name="terminal"),
            Tool(name=TerminalTool.name),
        ],
    ).create_agent()
    return {
        "default": tool_names(default),
        "bare": tool_names(bare),
        "bare_count": len(bare.tools),
        "dup_terminal_count": sum(1 for t in dup.tools if "erminal" in t.name),
        "dup": [t.name for t in dup.tools],
    }


@section("retired_switches")
def _():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        live = apply_agent_settings_diff(
            OpenHandsAgentSettings(llm=llm()), {"enable_sub_agents": True}
        )
        migrated = validate_agent_settings(
            {
                "schema_version": 6,
                "agent_kind": "openhands",
                "llm": {"model": "openai/settings-model"},
                "enable_sub_agents": True,
            }
        )
    dumped = live.model_dump(mode="json")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        in_code = OpenHandsAgentSettings(llm=llm(), enable_sub_agents=True)
    return {
        "in_code_tools": [t.name for t in in_code.tools or []],
        "live_tools": [t.name for t in live.tools or []],
        "migrated_tools": [t.name for t in migrated.tools or []],
        "same_set": [t.name for t in live.tools or []]
        == [t.name for t in migrated.tools or []],
        "serialized_keys": sorted(
            k
            for k in dumped
            if k.startswith("enable_sub") or k == "enable_switch_llm_tool"
        ),
    }


@section("condenser")
def _():
    on = OpenHandsAgentSettings(
        llm=llm(max_input_tokens=32000),
        condenser=LLMSummarizingCondenserSettings(
            enabled=True, max_size=80, keep_first=2
        ),
    ).create_agent()
    explicit = OpenHandsAgentSettings(
        llm=llm(max_input_tokens=32000),
        condenser=LLMSummarizingCondenserSettings(enabled=True, max_tokens=5000),
    ).create_agent()
    off = OpenHandsAgentSettings(
        llm=llm(), condenser=LLMSummarizingCondenserSettings(enabled=False)
    ).create_agent()
    noop = OpenHandsAgentSettings(
        llm=llm(), condenser=NoOpCondenserSettings()
    ).create_agent()
    legacy = validate_agent_settings(
        {
            "llm": {"model": "openai/settings-model"},
            "condenser": {"enabled": True, "max_size": 60},
        }
    )
    return {
        "on_type": type(on.condenser).__name__,
        "on_max_size": getattr(on.condenser, "max_size", None),
        "on_keep_first": getattr(on.condenser, "keep_first", None),
        "on_max_tokens": getattr(on.condenser, "max_tokens", None),
        "explicit_max_tokens": getattr(explicit.condenser, "max_tokens", None),
        "condenser_llm_usage_id": on.condenser.llm.usage_id,
        "off": off.condenser,
        "noop_type": type(noop.condenser).__name__,
        "legacy_type": type(legacy.condenser).__name__,
    }


@section("verification")
def _():
    with_key = OpenHandsAgentSettings(
        llm=llm(), verification=VerificationSettings(critic_enabled=True)
    ).create_agent()
    no_key = OpenHandsAgentSettings(
        llm=LLM(model="openai/settings-model", usage_id="agent"),
        verification=VerificationSettings(critic_enabled=True),
    ).create_agent()
    explicit = OpenHandsAgentSettings(
        llm=LLM(model="openai/settings-model", usage_id="agent"),
        verification=VerificationSettings(
            critic_enabled=True,
            critic_api_key="critic-key",
            enable_iterative_refinement=True,
            critic_threshold=0.7,
            max_refinement_iterations=2,
        ),
    )
    critic = explicit.create_agent().critic
    reloaded = OpenHandsAgentSettings.model_validate(
        explicit.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    )
    disabled = OpenHandsAgentSettings(llm=llm()).create_agent()
    key = reloaded.verification.critic_api_key
    return {
        "with_llm_key": type(with_key.critic).__name__,
        "without_key": no_key.critic,
        "disabled": disabled.critic,
        "refinement_threshold": getattr(
            getattr(critic, "iterative_refinement", None), "success_threshold", None
        ),
        "refinement_max": getattr(
            getattr(critic, "iterative_refinement", None), "max_iterations", None
        ),
        "key_roundtrip": (
            key.get_secret_value() if hasattr(key, "get_secret_value") else key
        )
        == "critic-key",
    }


@section("mcp_secrets")
def _():
    from openhands.sdk.utils.cipher import Cipher

    s = OpenHandsAgentSettings(llm=llm(), mcp_config=MCP)
    blob = s.model_dump_json()
    cipher = Cipher(secret_key="verify-cipher-key")
    enc = s.model_dump(mode="json", context={"cipher": cipher})
    enc_token = enc["mcp_config"]["github"]["env"]["GITHUB_TOKEN"]
    dec = OpenHandsAgentSettings.model_validate(enc, context={"cipher": cipher})
    wrong = None
    try:
        bad = OpenHandsAgentSettings.model_validate(
            enc, context={"cipher": Cipher(secret_key="other")}
        )
        wrong = (
            plain(bad.mcp_config["github"].env.get("GITHUB_TOKEN"))
            if bad.mcp_config["github"].env
            else None
        )
    except Exception as exc:
        wrong = type(exc).__name__
    legacy_plain = OpenHandsAgentSettings.model_validate(
        {"llm": {"model": "m"}, "mcp_config": {"github": MCP["github"]}},
        context={"cipher": cipher},
    )
    agent = s.create_agent()
    return {
        "redacted_in_dump": "ghp-secret" not in blob and "tok-secret" not in blob,
        "encrypted_differs": isinstance(enc_token, str)
        and enc_token != "ghp-secret"
        and enc_token != "**********",
        "decrypted": plain(dec.mcp_config["github"].env["GITHUB_TOKEN"]),
        "decrypted_header": plain(dec.mcp_config["remote"].headers["X-API-Token"]),
        "wrong_cipher": str(wrong),
        "legacy_plaintext": plain(
            legacy_plain.mcp_config["github"].env["GITHUB_TOKEN"]
        ),
        "agent_real_env": plain(agent.mcp_config["github"].env["GITHUB_TOKEN"]),
    }


@section("concurrency_persona")
def _():
    rejected = []
    for bad in (0, -1, "many"):
        try:
            OpenHandsAgentSettings(llm=llm(), tool_concurrency_limit=bad)
        except Exception:
            rejected.append(str(bad))
    default = OpenHandsAgentSettings(llm=llm())
    persona = OpenHandsAgentSettings(
        llm=llm(), persona="You are Captain Verify, a pirate engineer."
    ).create_agent()
    plain = default.create_agent()
    return {
        "default_limit": default.tool_concurrency_limit,
        "rejected": rejected,
        "four": OpenHandsAgentSettings(llm=llm(), tool_concurrency_limit=4)
        .create_agent()
        .tool_concurrency_limit,
        "persona_in_prompt": "Captain Verify" in persona.static_system_message,
        "builtin_in_plain_prompt": "OpenHands" in plain.static_system_message,
        "persona_replaces_builtin": "Captain Verify" in persona.static_system_message
        and plain.static_system_message.split("\n", 1)[0]
        != persona.static_system_message.split("\n", 1)[0],
    }


@section("subscription")
def _():
    s = validate_agent_settings(
        {
            "llm": {
                "model": "gpt-5.6",
                "auth_type": "subscription",
                "subscription_vendor": "openai",
            }
        }
    )
    dumped = s.model_dump(mode="json", exclude_none=True)
    return {
        "auth_type": s.llm.auth_type,
        "vendor": s.llm.subscription_vendor,
        "dumped_auth_type": dumped["llm"].get("auth_type"),
        "api_key_in_dump": "api_key" in dumped["llm"],
    }


@section("acp")
def _():
    import shutil

    from openhands.sdk.agent import ACPAgent

    claude = ACPAgentSettings(acp_server="claude-code", acp_model="claude-opus-4-6")
    agent = claude.create_agent()
    custom_error = None
    try:
        ACPAgentSettings(acp_server="custom").create_agent()
    except Exception as exc:
        custom_error = type(exc).__name__
    custom = ACPAgentSettings(
        acp_server="custom", acp_command=["python", "my_acp.py"]
    ).create_agent()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with_llm = ACPAgentSettings(
            llm=LLM(model="gpt-5.6", api_key=SecretStr("sk-explicit"))
        )
    dumped = with_llm.model_dump_json(context={"expose_secrets": "plaintext"})
    return {
        "type": type(agent).__name__,
        "is_acp": isinstance(agent, ACPAgent),
        "command": agent.acp_command,
        "pinned_binary_on_path": bool(shutil.which("claude-agent-acp")),
        "model": agent.acp_model,
        "server": agent.acp_server,
        "api_key_env": claude.api_key_env_var,
        "custom_without_command": custom_error,
        "custom_command": custom.acp_command,
        "llm_warned": any("ACPAgentSettings.llm" in str(w.message) for w in caught),
        "llm_dropped": "llm" not in with_llm.model_dump()
        and "sk-explicit" not in dumped,
    }


@section("dispatch")
def _():
    from openhands.sdk.settings.model import LLMAgentSettings

    oh = validate_agent_settings({"agent_kind": "openhands", "llm": {"model": "m"}})
    acp = validate_agent_settings({"agent_kind": "acp", "acp_server": "codex"})
    missing = validate_agent_settings({"llm": {"model": "m"}})
    legacy = validate_agent_settings({"agent_kind": "llm", "llm": {"model": "m"}})
    return {
        "openhands": type(oh).__name__,
        "acp": type(acp).__name__,
        "missing_kind": type(missing).__name__,
        "legacy_llm_kind": type(legacy).__name__,
        "legacy_is_llm_subclass": type(legacy) is LLMAgentSettings,
    }


@section("migrations")
def _():
    v0 = validate_agent_settings(
        {
            "llm": {
                "model": "litellm_proxy/claude-sonnet",
                "api_key": "k",
                "base_url": "https://llm-proxy.app.all-hands.dev",
            },
            "mcp_config": {
                "mcpServers": {
                    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
                }
            },
        }
    )
    rejected = {}
    for v in (AGENT_SETTINGS_SCHEMA_VERSION + 1, -1, "seven"):
        try:
            validate_agent_settings({"schema_version": v, "llm": {"model": "m"}})
            rejected[str(v)] = False
        except Exception:
            rejected[str(v)] = True
    current = OpenHandsAgentSettings(llm=llm()).model_dump(
        mode="json", context={"expose_secrets": "plaintext"}
    )
    return {
        "current_version": AGENT_SETTINGS_SCHEMA_VERSION,
        "v0_loaded": type(v0).__name__,
        "v0_dump_version": v0.model_dump(mode="json")["schema_version"],
        "v0_mcp_server_map": sorted(v0.mcp_config),
        "rejected": rejected,
        "persisted_equals_validate": OpenHandsAgentSettings.from_persisted(
            current
        ).model_dump(mode="json")
        == OpenHandsAgentSettings.model_validate(current).model_dump(mode="json"),
    }


@section("conversation_settings")
def _():
    from openhands.agent_server.models import StartConversationRequest
    from openhands.sdk.security.confirmation_policy import AlwaysConfirm, ConfirmRisky
    from openhands.sdk.workspace import LocalWorkspace

    cs = ConversationSettings(
        max_iterations=77, confirmation_mode=True, security_analyzer="llm"
    )
    dumped = cs.model_dump(mode="json")
    back = ConversationSettings.from_persisted(dumped)
    v0 = ConversationSettings.from_persisted({"max_iterations": 12})
    newer = None
    try:
        ConversationSettings.from_persisted(
            {"schema_version": CONVERSATION_SETTINGS_SCHEMA_VERSION + 1}
        )
    except Exception as exc:
        newer = type(exc).__name__
    agent = OpenHandsAgentSettings(llm=llm()).create_agent()
    req = cs.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(run_paths()["workspace"])),
    )
    over = cs.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(run_paths()["workspace"])),
        max_iterations=5,
        confirmation_policy=AlwaysConfirm(),
        security_analyzer=None,
    )
    return {
        "schema_version": dumped.get("schema_version"),
        "roundtrip": back == cs,
        "v0_max_iterations": v0.max_iterations,
        "newer_rejected": newer,
        "request_max_iterations": req.max_iterations,
        "request_policy": type(req.confirmation_policy).__name__,
        "request_policy_is_risky": isinstance(req.confirmation_policy, ConfirmRisky),
        "request_analyzer": type(req.security_analyzer).__name__,
        "override_max_iterations": over.max_iterations,
        "override_policy": type(over.confirmation_policy).__name__,
        "override_analyzer": over.security_analyzer,
    }


@section("apply_diff")
def _():
    base = OpenHandsAgentSettings(
        llm=llm(),
        persona="Pirate",
        condenser=LLMSummarizingCondenserSettings(enabled=True, max_size=40),
    )
    merged = apply_agent_settings_diff(
        base, {"condenser": {"max_size": 90}, "llm": {"model": "openai/new"}}
    )
    unset = apply_agent_settings_diff(base, {"persona": None})
    switched = apply_agent_settings_diff(
        base, {"agent_kind": "acp", "acp_server": "codex"}
    )
    return {
        "merged_max_size": merged.condenser.max_size,
        "merged_enabled_kept": merged.condenser.enabled,
        "merged_model": merged.llm.model,
        "merged_key_kept": merged.llm.api_key is not None
        and merged.llm.api_key.get_secret_value() == "sk-llm",
        "persona_unset": unset.persona,
        "switched_type": type(switched).__name__,
        "switched_has_persona": hasattr(switched, "persona"),
    }


@section("schema")
def _():
    schema = export_agent_settings_schema()
    data = schema.model_dump(mode="json")
    sections = data.get("sections", [])
    variants = sorted({s.get("variant") for s in sections if s.get("variant")})
    fields = [f for s in sections for f in s.get("fields", [])]
    conv = export_settings_schema(ConversationSettings).model_dump(mode="json")
    return {
        "sections": len(sections),
        "variants": variants,
        "has_llm_model_field": any(f.get("key", "").endswith("model") for f in fields),
        "labels": all(f.get("label") for f in fields),
        "prominence_values": sorted({str(f.get("prominence")) for f in fields}),
        "conversation_sections": len(conv.get("sections", [])),
    }


@section("conversation")
def _():
    """The built agent runs: settings tools and persona drive a scripted turn."""
    settings = OpenHandsAgentSettings(
        llm=llm(),
        tools=[Tool(name=TerminalTool.name)],
        persona="You are Captain Verify.",
    )
    agent = settings.create_agent()
    agent = agent.model_copy(
        update={
            "llm": make_llm(
                [
                    {
                        "tool": "terminal",
                        "args": {"command": "echo built-from-settings > settings.txt"},
                    },
                    {"finish": "done"},
                ]
            )
        }
    )
    conv = Conversation(
        agent=agent, workspace=str(run_paths()["workspace"]), visualizer=None
    )
    conv.send_message("write settings.txt")
    conv.run()
    from openhands.sdk.event import SystemPromptEvent

    system = next(e for e in conv.state.events if isinstance(e, SystemPromptEvent))
    status = conv.state.execution_status.value
    conv.close()
    f = run_paths()["workspace"] / "settings.txt"
    return {
        "status": str(status),
        "file": f.read_text().strip() if f.exists() else None,
        "persona_in_system_prompt": "Captain Verify" in system.system_prompt.text,
        "tool_names": sorted(t.name for t in system.tools),
    }


emit(out)
