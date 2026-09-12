"""Resolve selected configuration on the outer side of the runtime boundary."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import (
    _resolve_agent_from_profile,
    _with_load_memory,
)
from openhands.agent_server.docker_runtime.provisioning import (
    RuntimeGrants,
    RuntimeIdentity,
    RuntimeProvisioningStore,
)
from openhands.agent_server.persistence import (
    get_llm_profile_store,
    get_secrets_store,
    get_settings_store,
)
from openhands.sdk import LLM
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.acp_file_credentials import CODEX_AUTH_SECRET_NAME
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPOAuthAuthCredential, MCPServer
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import LookupSecret, StaticSecret
from openhands.sdk.settings.model import validate_agent_settings


def _materialize(value: Any, config: Config) -> Any:
    if isinstance(value, LookupSecret):
        parsed = urlsplit(value.url)
        prefix = "/api/settings/secrets/"
        allowed = urlsplit(os.getenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8000"))
        if parsed.netloc != allowed.netloc or parsed.scheme != allowed.scheme:
            raise ValueError(
                "External secret lookups are unsupported in Docker runtimes"
            )
        if not parsed.path.startswith(prefix) or parsed.query or parsed.fragment:
            raise ValueError(
                "Docker secret references must select a saved secret by name"
            )
        name = unquote(parsed.path[len(prefix) :])
        if not name or "/" in name:
            raise ValueError("Invalid selected secret reference")
        secret = get_secrets_store(config).get_secret(name)
        if secret is None:
            raise ValueError("Selected secret is unavailable")
        return StaticSecret(value=SecretStr(secret), description=value.description)
    if isinstance(value, MCPOAuthAuthCredential):
        return value.model_copy(update={"state": None, "authentication": None})
    if isinstance(value, BaseModel):
        updates = {
            name: _materialize(getattr(value, name), config)
            for name in type(value).model_fields
        }
        return value.model_copy(update=updates)
    if isinstance(value, dict):
        return {key: _materialize(item, config) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item, config) for item in value]
    if isinstance(value, tuple):
        return tuple(_materialize(item, config) for item in value)
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if isinstance(raw, str):
        protected = [*config.session_api_keys]
        if config.secret_key is not None:
            protected.append(config.secret_key.get_secret_value())
        if any(key and key in raw for key in protected):
            raise ValueError(
                "Outer control credentials cannot be provisioned to a runtime"
            )
    return value


def grants_for_agent(agent: BaseModel) -> RuntimeGrants:
    subscription = None
    mcp_names: set[str] = set()

    def visit(value: Any):
        nonlocal subscription
        if isinstance(value, LLM) and (
            value.is_subscription or value.auth_type == "subscription"
        ):
            subscription = "openai"
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                visit(getattr(value, name))
        elif isinstance(value, dict):
            for name, item in value.items():
                if isinstance(item, MCPServer) and isinstance(
                    item.auth, MCPOAuthAuthCredential
                ):
                    mcp_names.add(name)
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(agent)
    credential_names = (
        frozenset({CODEX_AUTH_SECRET_NAME})
        if isinstance(agent, ACPAgent) and agent.acp_server == "codex"
        else frozenset()
    )
    return RuntimeGrants(
        subscription=subscription,
        credential_names=credential_names,
        mcp_server_names=frozenset(mcp_names),
    )


async def materialize_start(
    body: dict[str, Any], config: Config
) -> tuple[StartConversationRequest, LaunchedAgentProfile | None]:
    context = {"cipher": config.cipher} if body.get("secrets_encrypted") else None
    if body.get("agent_settings") is not None:
        settings = validate_agent_settings(body["agent_settings"], context=context)
        body = {**body, "agent": settings.create_agent(), "agent_settings": None}
    request = StartConversationRequest.model_validate(body, context=context)
    launched = None
    settings = await asyncio.to_thread(get_settings_store(config).load)
    if request.agent_profile_id is not None:
        mcp_config = settings.agent_settings.mcp_config if settings is not None else {}
        agent, launched = await asyncio.to_thread(
            _resolve_agent_from_profile,
            request.agent_profile_id,
            config.cipher,
            mcp_config,
            acp_skill_sourcing="openhands_managed",
        )
        request = request.model_copy(update={"agent": agent, "agent_profile_id": None})
    if (
        settings is not None
        and settings.agent_settings.agent_context is not None
        and settings.agent_settings.agent_context.load_memory
    ):
        request = request.model_copy(update={"agent": _with_load_memory(request.agent)})
    return await asyncio.to_thread(_materialize, request, config), launched


def serialize_for_runtime(
    request: StartConversationRequest, identity: RuntimeIdentity
) -> dict[str, Any]:
    data = request.model_dump(
        mode="json", context={"cipher": identity.cipher}, exclude={"agent_profile_id"}
    )
    data["secrets_encrypted"] = True
    return data


async def mediate_mutation(
    tail: str, body: dict[str, Any], config: Config, identity: RuntimeIdentity
) -> tuple[str, dict[str, Any], RuntimeGrants | None]:
    from openhands.agent_server._secrets_exposure import decrypt_incoming_llm_secrets
    from openhands.agent_server.models import (
        SetSecurityAnalyzerRequest,
        UpdateSecretsRequest,
    )
    from openhands.agent_server.persistence import get_llm_profile_store

    if tail == "security_analyzer":
        analyzer = SetSecurityAnalyzerRequest.model_validate(
            body, context={"cipher": config.cipher}
        )
        resolved = await asyncio.to_thread(_materialize, analyzer, config)
        return (
            tail,
            resolved.model_dump(mode="json", context={"expose_secrets": True}),
            None,
        )
    if tail == "secrets":
        secrets = UpdateSecretsRequest.model_validate(
            body, context={"cipher": config.cipher}
        )
        resolved = await asyncio.to_thread(_materialize, secrets, config)
        return (
            tail,
            resolved.model_dump(mode="json", context={"expose_secrets": True}),
            None,
        )
    if tail == "switch_profile":
        name = body.get("profile_name")
        if not isinstance(name, str):
            raise ValueError("Profile name is required")
        llm = await asyncio.to_thread(
            get_llm_profile_store().load, name, cipher=config.cipher
        )
    elif tail == "switch_llm":
        llm = LLM.model_validate(body.get("llm"))
        if config.cipher is not None:
            llm = decrypt_incoming_llm_secrets(llm, config.cipher)
    else:
        raise ValueError("Unsupported credential mutation")
    llm = _materialize(llm, config)
    grants = identity.grants.model_copy(
        update={
            "subscription": "openai"
            if grants_for_agent(llm).subscription or identity.auxiliary_subscription
            else None
        }
    )
    return (
        "switch_llm",
        {"llm": llm.model_dump(mode="json", context={"cipher": identity.cipher})},
        grants,
    )


async def materialize_title_profile(
    request: StartConversationRequest,
    store: RuntimeProvisioningStore,
    identity: RuntimeIdentity,
) -> RuntimeIdentity:
    if request.title_llm_profile is None:
        return identity
    llm = await asyncio.to_thread(
        get_llm_profile_store().load,
        request.title_llm_profile,
        cipher=store.config.cipher,
    )
    llm = _materialize(llm, store.config).model_copy(
        update={"provider_connection_id": None}
    )
    destination = (
        store.runtime_dir(identity.conversation_id) / "persistence" / "profiles"
    )
    await asyncio.to_thread(
        LLMProfileStore(destination).save,
        request.title_llm_profile,
        llm,
        include_secrets=True,
        cipher=identity.cipher,
    )
    if llm.is_subscription:
        identity = identity.model_copy(
            update={
                "grants": identity.grants.model_copy(update={"subscription": "openai"}),
                "auxiliary_subscription": True,
            }
        )
    return identity


def has_auxiliary_subscription(request: StartConversationRequest) -> bool:
    agent = request.agent
    for name in type(agent).model_fields:
        if name != "llm":
            value = getattr(agent, name)
            if _has_subscription(value):
                return True
    analyzer = request.security_analyzer
    return bool(analyzer and grants_for_agent(analyzer).subscription)


async def materialize_mcp_probe(body: dict[str, Any], config: Config) -> dict[str, Any]:
    from openhands.agent_server.mcp_router import MCPTestRequest

    probe = MCPTestRequest.model_validate(body, context={"cipher": config.cipher})
    resolved = await asyncio.to_thread(_materialize, probe, config)
    return resolved.model_dump(mode="json", context={"expose_secrets": True})


def _has_subscription(value: Any) -> bool:
    if isinstance(value, BaseModel):
        return grants_for_agent(value).subscription is not None
    if isinstance(value, dict):
        return any(_has_subscription(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_subscription(item) for item in value)
    return False
