"""Every agent-settings field has exactly one owner in the launch pipeline.

An unclassified field fails here, before it can reach one launch path and not
another: a field added to the settings but not to the profile is caught the day
it lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.launch import resolve
from openhands.sdk.llm.meta_profile_store import MetaProfile, MetaProfileClass
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    OpenHandsAgentProfile,
    ProfileVerificationSettings,
)
from openhands.sdk.settings.model import (
    ACPAgentSettings,
    NoOpCondenserSettings,
    OpenHandsAgentSettings,
    VerificationSettings,
)
from openhands.sdk.tool import Tool
from tests.sdk.launch import fakes


@dataclass(frozen=True)
class Profile:
    """Copied from the profile field of the same name."""


@dataclass(frozen=True)
class Reference:
    """Resolved from the named profile reference."""

    ref: str


@dataclass(frozen=True)
class Fixed:
    """Set to ``value`` by resolve for every profile."""

    value: Any


@dataclass(frozen=True)
class Launch:
    """Set by finalize, or later by the running conversation."""


@dataclass(frozen=True)
class NotOnProfile:
    """Settings-only: a profile launch gets the default. Must say why."""

    reason: str


@dataclass(frozen=True)
class Legacy:
    removed_in: str


@dataclass(frozen=True)
class Nested:
    fields: dict[str, Any]


STRUCTURAL = NotOnProfile("schema metadata")
PROFILE = Profile()
LAUNCH = Launch()

_CATALOG = Fixed(False)

OPENHANDS_CONTEXT = {
    "skills": Reference("disabled_skills"),
    "system_message_suffix": PROFILE,
    "user_message_suffix": NotOnProfile("no profile field yet"),
    "load_user_skills": _CATALOG,
    "load_public_skills": _CATALOG,
    "marketplace_path": NotOnProfile("only read when load_public_skills is set"),
    "registered_marketplaces": NotOnProfile(
        "marketplace plugins are server settings, not yet applied to profiles"
    ),
    "load_project_skills": Fixed(True),
    "load_memory": LAUNCH,
    "memory_context": LAUNCH,
    "disabled_skills": PROFILE,
    "secrets": NotOnProfile("a profile is secret-free; secrets ride the request"),
    "current_datetime": LAUNCH,
}

OPENHANDS_FIELDS: dict[str, Any] = {
    "schema_version": STRUCTURAL,
    "agent_kind": STRUCTURAL,
    "agent": PROFILE,
    "llm": Reference("llm_profile_ref"),
    "tools": PROFILE,
    "enable_sub_agents": PROFILE,
    "enable_switch_llm_tool": PROFILE,
    "enable_classify_and_switch_llm_tool": PROFILE,
    "active_meta_profile": Reference("meta_profile_ref"),
    "meta_profile": Reference("meta_profile_ref"),
    "meta_profile_llms": Reference("meta_profile_ref"),
    "tool_concurrency_limit": PROFILE,
    "mcp_config": Reference("mcp_server_refs"),
    "agent_context": Nested(OPENHANDS_CONTEXT),
    "condenser": PROFILE,
    "verification": Nested(
        {
            **{name: PROFILE for name in ProfileVerificationSettings.model_fields},
            "critic_api_key": NotOnProfile(
                "a profile is secret-free; the critic reuses the resolved LLM key"
            ),
        }
    ),
}

ACP_CONTEXT = {
    **OPENHANDS_CONTEXT,
    "skills": Reference("catalog"),
    "system_message_suffix": NotOnProfile("the ACP CLI owns its prompt"),
    "disabled_skills": NotOnProfile("the ACP CLI owns its skills"),
    "load_project_skills": Fixed(False),
    "current_datetime": Fixed(None),
}

ACP_FIELDS: dict[str, Any] = {
    "schema_version": STRUCTURAL,
    "agent_kind": STRUCTURAL,
    "acp_server": PROFILE,
    "acp_command": PROFILE,
    "acp_args": PROFILE,
    "acp_model": PROFILE,
    "acp_session_mode": PROFILE,
    "acp_prompt_timeout": PROFILE,
    "acp_startup_timeout": PROFILE,
    "mcp_config": Reference("mcp_server_refs"),
    "acp_isolate_data_dir": NotOnProfile(
        "set by the deploying application when conversations share a sandbox"
    ),
    "acp_file_secrets": NotOnProfile("a code-level spec for other ACP CLIs"),
    "llm": Legacy(removed_in="1.33.0"),
    "agent_context": Nested(ACP_CONTEXT),
}

PROFILE_IDENTITY = {"schema_version", "id", "name", "revision", "agent_kind"}
PROFILE_SCOPE = {"secret_refs"}

SETTINGS_MODELS: dict[type[BaseModel], dict[str, Any]] = {
    OpenHandsAgentSettings: OPENHANDS_FIELDS,
    ACPAgentSettings: ACP_FIELDS,
}
NESTED_MODELS: dict[str, type[BaseModel]] = {
    "agent_context": AgentContext,
    "verification": VerificationSettings,
}


def _leaves(table: dict[str, Any], prefix: str = ""):
    for name, owner in table.items():
        if isinstance(owner, Nested):
            yield from _leaves(owner.fields, f"{prefix}{name}.")
        else:
            yield f"{prefix}{name}", owner


def _get(model: Any, path: str) -> Any:
    for part in path.split("."):
        model = getattr(model, part)
    return model


@pytest.mark.parametrize(
    "settings_model", list(SETTINGS_MODELS), ids=lambda m: m.__name__
)
def test_every_settings_field_is_classified(settings_model):
    table = SETTINGS_MODELS[settings_model]
    assert set(table) == set(settings_model.model_fields)
    for name, owner in table.items():
        if isinstance(owner, Nested):
            assert set(owner.fields) == set(NESTED_MODELS[name].model_fields), name


@pytest.mark.parametrize(
    ("settings_model", "profile_model"),
    [
        (OpenHandsAgentSettings, OpenHandsAgentProfile),
        (ACPAgentSettings, ACPAgentProfile),
    ],
    ids=["openhands", "acp"],
)
def test_every_profile_owned_field_exists_on_the_profile(settings_model, profile_model):
    for path, owner in _leaves(SETTINGS_MODELS[settings_model]):
        if isinstance(owner, Profile):
            field = path.removeprefix("agent_context.")
            head = field.split(".")[0]
            assert head in profile_model.model_fields, path
        if isinstance(owner, Reference) and owner.ref != "catalog":
            assert owner.ref in profile_model.model_fields, path


@pytest.mark.parametrize(
    ("settings_model", "profile_model"),
    [
        (OpenHandsAgentSettings, OpenHandsAgentProfile),
        (ACPAgentSettings, ACPAgentProfile),
    ],
    ids=["openhands", "acp"],
)
def test_every_profile_field_has_a_use(settings_model, profile_model):
    used = PROFILE_IDENTITY | PROFILE_SCOPE
    for path, owner in _leaves(SETTINGS_MODELS[settings_model]):
        if isinstance(owner, Profile):
            used.add(path.removeprefix("agent_context.").split(".")[0])
        if isinstance(owner, Reference):
            used.add(owner.ref)
    assert set(profile_model.model_fields) <= used


def _non_default_openhands_profile() -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="default",
        agent="PlanningAgent",
        tools=[Tool(name="terminal")],
        system_message_suffix="suffix",
        disabled_skills=["beta"],
        condenser=NoOpCondenserSettings(),
        verification=ProfileVerificationSettings(
            critic_enabled=True,
            critic_mode="all_actions",
            enable_iterative_refinement=True,
            critic_threshold=0.9,
            max_refinement_iterations=5,
            critic_server_url="https://critic.invalid",
            critic_model_name="critic",
        ),
        enable_sub_agents=True,
        enable_switch_llm_tool=False,
        enable_classify_and_switch_llm_tool=True,
        meta_profile_ref="router",
        tool_concurrency_limit=3,
    )


def _non_default_acp_profile() -> ACPAgentProfile:
    return ACPAgentProfile(
        name="a",
        acp_server="codex",
        acp_model="gpt-5.5",
        acp_session_mode="full-access",
        acp_prompt_timeout=12.0,
        acp_startup_timeout=34.0,
        acp_command="codex-acp --flag",
        acp_args=["--x"],
    )


_PROFILE_TO_SETTINGS_VALUE = {"acp_command": lambda value: value.split()}


def _stores():
    meta = MetaProfile(
        classifier_model="default",
        classes=[MetaProfileClass(description="all", model="default")],
    )
    return fakes.stores(mcp={"srv": MCPServer(command="echo")}, metas={"router": meta})


@pytest.mark.parametrize(
    ("profile", "table"),
    [
        (_non_default_openhands_profile(), OPENHANDS_FIELDS),
        (_non_default_acp_profile(), ACP_FIELDS),
    ],
    ids=["openhands", "acp"],
)
def test_resolve_copies_profile_fields_and_fixed_values(profile, table):
    settings = resolve(profile, _stores()).settings

    for path, owner in _leaves(table):
        if isinstance(owner, Profile):
            field = path.removeprefix("agent_context.")
            value = _get(profile, field)
            expected = _PROFILE_TO_SETTINGS_VALUE.get(field, lambda v: v)(value)
            assert _get(settings, path) == expected, path
        if isinstance(owner, Fixed):
            assert _get(settings, path) == owner.value, path
