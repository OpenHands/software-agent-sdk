"""Prepare a conversation request for an isolated agent-server."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from openhands.agent_server.agent_launch import (
    LaunchSource,
    apply_launch,
    launch_runtime,
    load_launch_source,
)
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.provisioning import RuntimeIdentity
from openhands.agent_server.docker_runtime.registry import ConversationContainer
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.profiles import AgentLaunchRuntime
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import SecretSource, SecretValue, StaticSecret
from openhands.sdk.tool import BROWSER_TOOL_NAME


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


def container_launch_runtime(
    settings: PersistedSettings, *, browser_available: bool
) -> AgentLaunchRuntime:
    # A container has no host home configuration to read skills from.
    return launch_runtime(
        settings,
        acp_skill_sourcing="openhands_managed",
        browser_available=browser_available,
    )


async def container_browser_available(container: ConversationContainer) -> bool:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{container.host}/server_info")
    response.raise_for_status()
    return BROWSER_TOOL_NAME in response.json().get("usable_tools", [])


@dataclass(frozen=True, kw_only=True)
class PreparedStart:
    """A start request whose launch was checked before its container starts."""

    request: StartConversationRequest
    source: LaunchSource
    settings: PersistedSettings
    launched: LaunchedAgentProfile | None


async def prepare_start(body: dict[str, Any], config: Config) -> PreparedStart:
    """Load the launch source and check that it resolves.

    Resolution runs without building the agent, so a dangling reference fails
    before a container is started.
    """
    body = {
        name: value
        for name, value in body.items()
        if value is not None or name not in {"agent", "agent_settings"}
    }
    context = {"cipher": config.cipher} if body.get("secrets_encrypted") else None
    request = StartConversationRequest.model_validate(body, context=context)

    try:
        settings = await asyncio.to_thread(get_settings_store(config).load)
    except (OSError, PermissionError):
        settings = None
    settings = settings or PersistedSettings()
    runtime = container_launch_runtime(settings, browser_available=False)
    source = await asyncio.to_thread(
        load_launch_source,
        request,
        cipher=config.cipher,
        settings=settings,
        runtime=runtime,
    )
    scoped, plan = await asyncio.to_thread(
        apply_launch, request, source, runtime, build_agent=False
    )
    secrets = await asyncio.to_thread(materialize_secrets, scoped.secrets)
    return PreparedStart(
        request=request.model_copy(update={"secrets": secrets}),
        source=source,
        settings=settings,
        launched=plan.launched_profile,
    )


async def finish_start(
    prepared: PreparedStart, runtime: AgentLaunchRuntime
) -> StartConversationRequest:
    """Build the agent for the container's ``runtime``."""
    request, _ = await asyncio.to_thread(
        apply_launch, prepared.request, prepared.source, runtime
    )
    agent = await asyncio.to_thread(_materialize_agent_context, request.agent)
    return request.model_copy(update={"agent": agent})


def serialize_start(
    request: StartConversationRequest, identity: RuntimeIdentity
) -> dict[str, Any]:
    payload = request.model_dump(
        mode="json",
        context={"cipher": identity.cipher},
        exclude={
            "agent_profile_id",
            "agent_profile",
            "agent_settings",
            "agent_launch_additions",
        },
    )
    payload["secrets_encrypted"] = True
    return payload
