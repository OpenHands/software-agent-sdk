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
from openhands.agent_server.docker_runtime.provisioning import (
    RuntimeIdentity,
    RuntimeProvisioningStore,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.secret import SecretSource, SecretValue, StaticSecret
from openhands.sdk.settings.model import validate_agent_settings


logger = get_logger(__name__)


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


def stage_title_profile(
    request: StartConversationRequest,
    identity: RuntimeIdentity,
    provisioning: RuntimeProvisioningStore,
    config: Config,
) -> None:
    """Copy the selected title LLM profile into the runtime's own profile store.

    The inner agent-server resolves ``title_llm_profile`` by name against the
    profile store under its own persistence directory, which is this
    conversation's private runtime directory rather than the host store. Only
    the selected profile is copied. A linked provider connection is resolved on
    the host into inline credentials, because the runtime has no provider store
    to follow the reference, and the copy is encrypted with the runtime key so
    the host key never enters the container.

    The copy is taken once, when the conversation is first established, like
    the agent's own LLM: a repeated start of an established conversation keeps
    the original snapshot, whatever the host profile or the new request says.
    A retry of a creation attempt that never established the conversation
    replaces what the earlier attempt staged, so the runtime store never holds
    more than the selected profile. A missing or unloadable profile is left to
    the inner server's existing fallback (agent LLM, then truncation), so
    conversation creation still succeeds.
    """
    conversation_id = identity.conversation_id
    # Establishment is judged by the same persisted markers
    # ``RuntimeProvisioningStore.create`` checks; the inner server writes them
    # into the mounted conversation directory once the start succeeded.
    conversation_dir = provisioning.direct_child(
        config.conversations_path, conversation_id.hex
    )
    profiles_dir = provisioning.persistence_dir(conversation_id) / "profiles"
    name = request.title_llm_profile
    with provisioning.lock(conversation_id):
        if (conversation_dir / "meta.json").exists() or (
            conversation_dir / "base_state.json"
        ).exists():
            return
        llm = _load_title_profile(name, config) if name else None
        if llm is None and not profiles_dir.is_dir():
            return
        profiles_dir.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        runtime_store = LLMProfileStore(base_dir=profiles_dir)
        for stale in runtime_store.list():
            runtime_store.delete(stale)
        if llm is not None and name:
            runtime_store.save(
                name,
                llm.model_copy(update={"provider_connection_id": None}),
                include_secrets=True,
                cipher=identity.cipher,
            )


def _load_title_profile(name: str, config: Config) -> LLM | None:
    try:
        return get_llm_profile_store().load(name, cipher=config.cipher)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            f"Title LLM profile '{name}' was not staged for the conversation "
            f"runtime: {exc}. The runtime will fall back to the agent's LLM."
        )
        return None


def serialize_start(
    request: StartConversationRequest, identity: RuntimeIdentity
) -> dict[str, Any]:
    payload = request.model_dump(
        mode="json", context={"cipher": identity.cipher}, exclude={"agent_profile_id"}
    )
    payload["secrets_encrypted"] = True
    return payload
