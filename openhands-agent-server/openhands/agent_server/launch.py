"""This server's side of the launch pipeline in :mod:`openhands.sdk.launch`.

Resolves a start request's agent source with this server's stores, describes
this process as a ``LaunchRuntime``, and serializes a resolved source for a
conversation container to finalize.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException, status
from pydantic import TypeAdapter

from openhands.agent_server.config import ACPSkillSourcing, Config
from openhands.agent_server.docker_runtime.mediation import materialize_secrets
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
)
from openhands.agent_server.skills_service import discover_profile_skills
from openhands.sdk.agent import ACPAgent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.launch import (
    AgentLaunchError,
    LaunchRuntime,
    LaunchSource,
    LaunchStoreError,
    LaunchStores,
    ResolvedLaunch,
    resolve,
)
from openhands.sdk.llm.meta_profile_store import (
    MetaProfileStore,
    default_meta_profile_dir,
)
from openhands.sdk.profiles.resolver import ProfileNotFound
from openhands.sdk.secret import SecretSource
from openhands.sdk.settings.model import (
    ACPAgentSettings,
    validate_agent_settings,
)
from openhands.sdk.tool import BROWSER_TOOL_NAME, is_tool_usable
from openhands.sdk.utils.cipher import Cipher


_SECRETS_ADAPTER: TypeAdapter[dict[str, SecretSource]] = TypeAdapter(
    dict[str, SecretSource]
)


def server_launch_stores(
    settings: PersistedSettings, cipher: Cipher | None
) -> LaunchStores:
    llm_store = get_llm_profile_store()
    return LaunchStores(
        llm_profiles=llm_store,
        llm_profile_names=lambda: [n.removesuffix(".json") for n in llm_store.list()],
        mcp_config=settings.agent_settings.mcp_config,
        skills=discover_profile_skills,
        agent_profiles=get_agent_profile_store(),
        meta_profiles=MetaProfileStore(base_dir=default_meta_profile_dir()),
        cipher=cipher,
    )


def live_launch_runtime(acp_skill_sourcing: ACPSkillSourcing) -> LaunchRuntime:
    return LaunchRuntime(
        browser_available=is_tool_usable(BROWSER_TOOL_NAME),
        acp_skill_sourcing=acp_skill_sourcing,
    )


def target_launch_runtime(config: Config) -> LaunchRuntime:
    """Describe the runtime this server's launches run in, without starting it."""
    if config.conversation_runtime == "docker":
        # The container image sets managed sourcing; whether it can run the
        # browser is known only once it starts.
        return LaunchRuntime(
            browser_available=None, acp_skill_sourcing="openhands_managed"
        )
    return live_launch_runtime(config.acp_skill_sourcing)


def launch_source(
    request: StartConversationRequest,
    stores: Callable[[], LaunchStores],
    cipher: Cipher | None,
) -> LaunchSource:
    """Turn the request's agent source into a launch source (blocking)."""
    context = _decryption_context(request, cipher)
    additions = request.agent_launch_additions
    llm_profile_ref = additions.llm_profile_ref if additions else None
    if request.agent_profile_id is not None:
        return resolve(
            request.agent_profile_id, stores(), llm_profile_ref=llm_profile_ref
        )
    if request.agent_profile is not None:
        return resolve(request.agent_profile, stores(), llm_profile_ref=llm_profile_ref)
    agent: AgentBase | None = request.agent
    if agent is not None:
        if context is None:
            return agent
        return type(agent).model_validate(
            agent.model_dump(mode="json", context={"expose_secrets": True}),
            context=context,
        )
    if request.agent_settings is None:
        raise AgentLaunchError("The start request has no agent source")
    try:
        settings = validate_agent_settings(request.agent_settings, context=context)
    except (TypeError, ValueError) as exc:
        raise AgentLaunchError(f"Invalid agent_settings: {exc}") from exc
    return ResolvedLaunch(settings=settings)


def request_secrets(
    request: StartConversationRequest, cipher: Cipher | None
) -> dict[str, SecretSource]:
    """The request's secrets, decrypted when the client encrypted them."""
    context = _decryption_context(request, cipher)
    if context is None:
        return dict(request.secrets)
    return _SECRETS_ADAPTER.validate_python(
        _SECRETS_ADAPTER.dump_python(
            request.secrets, mode="json", context={"expose_secrets": True}
        ),
        context=context,
    )


def _decryption_context(
    request: StartConversationRequest, cipher: Cipher | None
) -> dict[str, Any] | None:
    if not request.secrets_encrypted:
        return None
    if cipher is None:
        raise ValueError(
            "Cannot decrypt secrets: cipher not configured. "
            "Set OH_SECRET_KEY environment variable."
        )
    return {"cipher": cipher}


LaunchFailure = ProfileNotFound | AgentLaunchError | LaunchStoreError


def launch_http_exception(exc: LaunchFailure) -> HTTPException:
    if isinstance(exc, ProfileNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, AgentLaunchError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.to_detail())
    code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if exc.retryable
        else status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    return HTTPException(code, str(exc))


def scoped_secrets(
    secrets: Mapping[str, SecretSource], source: LaunchSource
) -> dict[str, SecretSource]:
    """Drop the request secrets a profile's ``secret_refs`` does not allow."""
    allowed = source.allowed_secrets if isinstance(source, ResolvedLaunch) else None
    if allowed is None:
        return dict(secrets)
    return {name: value for name, value in secrets.items() if name in allowed}


def is_codex_source(source: LaunchSource) -> bool:
    if isinstance(source, ResolvedLaunch):
        settings = source.settings
        return isinstance(settings, ACPAgentSettings) and settings.acp_server == "codex"
    return isinstance(source, ACPAgent) and source.acp_server == "codex"


def forward_to_runtime(
    request: StartConversationRequest,
    source: LaunchSource | None,
    secrets: Mapping[str, SecretSource],
    cipher: Cipher,
    *,
    load_memory: bool,
) -> dict[str, Any]:
    """Serialize a start request for a conversation container to finalize.

    ``secrets`` are the request's secrets already decrypted; those only this
    server can resolve are materialized, and the payload is encrypted with the
    container's ``cipher``. ``source`` None forwards the request's own profile
    source, for a conversation the container already has.
    """
    scoped = secrets if source is None else scoped_secrets(secrets, source)
    forwarded = request.model_copy(
        update={"secrets": materialize_secrets(scoped), "secrets_encrypted": True}
    )
    sources = {"agent", "agent_settings", "agent_profile_id", "agent_profile"}
    payload = forwarded.model_dump(
        mode="json",
        context={"cipher": cipher},
        exclude=sources if source is not None else {"agent", "agent_settings"},
    )
    if source is None:
        return payload
    model = source.settings if isinstance(source, ResolvedLaunch) else source
    field = "agent_settings" if isinstance(source, ResolvedLaunch) else "agent"
    payload[field] = model.model_copy(
        update=_forwarded_context(model.agent_context, load_memory=load_memory)
    ).model_dump(mode="json", context={"cipher": cipher})
    additions = dict(payload.get("agent_launch_additions") or {})
    # Already applied by resolve; without a profile source it is invalid.
    additions.pop("llm_profile_ref", None)
    payload["agent_launch_additions"] = additions or None
    return payload


def _forwarded_context(
    context: AgentContext | None, *, load_memory: bool
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if context is not None and context.secrets:
        updates["secrets"] = materialize_secrets(context.secrets)
    if load_memory:
        updates["load_memory"] = True
    if not updates:
        return {}
    # A null context means "no prompt context", so a synthesized one carries no
    # timestamp.
    base = context or AgentContext(current_datetime=None)
    return {"agent_context": base.model_copy(update=updates)}
