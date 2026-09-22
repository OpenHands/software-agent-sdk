"""Prepare a conversation request for an isolated agent-server."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import (
    _resolve_agent_from_profile,
    _with_load_memory,
)
from openhands.agent_server.docker_runtime.provisioning import RuntimeIdentity
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import SecretSource, SecretValue, StaticSecret
from openhands.sdk.settings.model import validate_agent_settings


def materialize_secrets(
    secrets: Mapping[str, SecretValue],
) -> dict[str, SecretSource]:
    """Resolve the SDK's secret sources before crossing a runtime boundary."""
    registry = SecretRegistry()
    registry.update_secrets(secrets)
    return {
        name: StaticSecret(
            value=SecretStr(value)
            if (value := registry.get_secret_value(name))
            else None,
            description=source.description,
        )
        for name, source in registry.secret_sources.items()
    }


def _materialize_agent_context(agent: AgentBase) -> AgentBase:
    context = agent.agent_context
    if context is None or not context.secrets:
        return agent
    return agent.model_copy(
        update={
            "agent_context": context.model_copy(
                update={"secrets": materialize_secrets(context.secrets)}
            )
        }
    )


async def prepare_start(
    body: dict[str, Any], config: Config
) -> tuple[StartConversationRequest, LaunchedAgentProfile | None]:
    body = {
        name: value
        for name, value in body.items()
        if value is not None or name not in {"agent", "agent_settings"}
    }
    context = {"cipher": config.cipher} if body.get("secrets_encrypted") else None
    if body.get("agent_settings") is not None:
        settings = validate_agent_settings(body["agent_settings"], context=context)
        body = {**body, "agent": settings.create_agent(), "agent_settings": None}
    request = StartConversationRequest.model_validate(body, context=context)

    try:
        settings = await asyncio.to_thread(get_settings_store(config).load)
    except (OSError, PermissionError):
        settings = None
    settings = settings or PersistedSettings()
    launched = None
    if request.agent_profile_id is not None:
        agent, launched, allowed = await asyncio.to_thread(
            _resolve_agent_from_profile,
            request.agent_profile_id,
            config.cipher,
            settings.agent_settings.mcp_config,
            acp_skill_sourcing=config.acp_skill_sourcing,
        )
        secrets = request.secrets
        if allowed is not None:
            secrets = {
                name: value for name, value in secrets.items() if name in allowed
            }
        request = request.model_copy(
            update={"agent": agent, "agent_profile_id": None, "secrets": secrets}
        )

    context_settings = settings.agent_settings.agent_context
    if context_settings is not None and context_settings.load_memory:
        request = request.model_copy(update={"agent": _with_load_memory(request.agent)})
    request = request.model_copy(
        update={
            "agent": await asyncio.to_thread(_materialize_agent_context, request.agent),
            "secrets": await asyncio.to_thread(materialize_secrets, request.secrets),
        }
    )
    return request, launched


def serialize_start(
    request: StartConversationRequest, identity: RuntimeIdentity
) -> dict[str, Any]:
    payload = request.model_dump(
        mode="json", context={"cipher": identity.cipher}, exclude={"agent_profile_id"}
    )
    payload["secrets_encrypted"] = True
    return payload
