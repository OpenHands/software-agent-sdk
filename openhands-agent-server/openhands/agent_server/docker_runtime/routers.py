"""Conversation routes for the Docker runtime."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.responses import JSONResponse, Response, StreamingResponse

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.credential_binding import LocalVersionedCredentialBinding
from openhands.agent_server.dependencies import (
    get_config,
    get_conversation_registry,
    get_conversation_service,
)
from openhands.agent_server.docker_runtime.mediation import materialize_secrets
from openhands.agent_server.docker_runtime.proxy import (
    bridge_websocket,
    proxy_http,
    strip_auth_query,
)
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
    RuntimeArchivedError,
)
from openhands.agent_server.event_router import require_feature_enabled
from openhands.agent_server.launch import (
    forward_to_runtime,
    is_codex_source,
    launch_error,
    launch_source,
    request_secrets,
    server_launch_stores,
)
from openhands.agent_server.models import (
    ConversationRuntimeInfo,
    ConversationRuntimeStatus,
    StartConversationRequest,
    StoredConversation,
    UpdateSecretsRequest,
)
from openhands.agent_server.persistence import PersistedSettings, run_store
from openhands.agent_server.utils import safe_rmtree
from openhands.sdk.agent import ACPAgent
from openhands.sdk.agent.acp_file_credentials import CODEX_AUTH_SECRET_NAME
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.conversation.request import ConversationContainerOptions
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.credential import (
    CredentialConflict,
    CredentialNeedsReauthentication,
    CredentialSyncError,
)
from openhands.sdk.errors import (
    Conflict,
    DomainError,
    Gone,
    Invalid,
    NotFound,
    NotSupported,
    Unauthenticated,
    Unavailable,
    Unprocessable,
    UpstreamFailed,
)
from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.git.utils import run_git_command, validate_git_repository
from openhands.sdk.launch import AgentLaunchError, LaunchStoreError, ResolvedLaunch
from openhands.sdk.logger import get_logger
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.profiles.resolver import ProfileNotFound
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import cipher_context, client_context
from openhands.sdk.workspace import LocalWorkspace


logger = get_logger(__name__)


_INTERNAL_SERVER_URL_ENV = "OH_INTERNAL_SERVER_URL"
_DEFAULT_INTERNAL_SERVER_URL = "http://127.0.0.1:8000"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"})


class RuntimeCredentialReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(min_length=1, max_length=1024)
    value: SecretStr


runtime_credential_broker_router = APIRouter(
    tags=["Docker Runtime Credentials"],
    include_in_schema=False,
)


def get_registry(registry: ConversationRegistry) -> DockerConversationRegistry:
    if not isinstance(registry, DockerConversationRegistry):
        raise Unavailable("Docker conversation runtime is unavailable")
    return registry


async def _runtime_credential_binding(
    registry: DockerConversationRegistry,
    conversation_id: UUID,
    secret_name: str,
    api_key: str | None,
) -> LocalVersionedCredentialBinding:
    """Authorize one runtime against its own host-side credential source."""
    if secret_name != CODEX_AUTH_SECRET_NAME or api_key is None:
        raise Unauthenticated()
    try:
        identity = await run_store(registry.provisioning.load, conversation_id)
    except ValueError as exc:
        raise Unauthenticated() from exc
    if not hmac.compare_digest(api_key, identity.api_key.get_secret_value()) or (
        identity.launched_agent_profile is not None
        and not identity.launched_agent_profile.allows_secret(secret_name)
    ):
        raise Unauthenticated()
    return LocalVersionedCredentialBinding(registry.stores.secrets, secret_name)


@runtime_credential_broker_router.get(
    "/{conversation_id}/runtime-credential-bindings/{secret_name}"
)
async def load_runtime_credential(
    conversation_id: UUID,
    secret_name: str,
    api_key: Annotated[str | None, Header(alias="X-Session-API-Key")] = None,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> JSONResponse:
    binding = await _runtime_credential_binding(
        get_registry(conversation_registry), conversation_id, secret_name, api_key
    )
    try:
        resolved = await binding.load()
    except CredentialNeedsReauthentication as exc:
        raise NotFound("Credential is missing. Please authenticate again.") from exc
    except CredentialSyncError as exc:
        raise UpstreamFailed("Credential store is unavailable") from exc
    return JSONResponse({"value": resolved.value, "version": resolved.version})


@runtime_credential_broker_router.put(
    "/{conversation_id}/runtime-credential-bindings/{secret_name}"
)
async def replace_runtime_credential(
    conversation_id: UUID,
    secret_name: str,
    replacement: RuntimeCredentialReplacement,
    api_key: Annotated[str | None, Header(alias="X-Session-API-Key")] = None,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> JSONResponse:
    binding = await _runtime_credential_binding(
        get_registry(conversation_registry), conversation_id, secret_name, api_key
    )
    try:
        version = await binding.replace(
            replacement.expected_version, replacement.value.get_secret_value()
        )
    except CredentialConflict as exc:
        raise Conflict("The canonical credential changed in another runtime") from exc
    except CredentialNeedsReauthentication as exc:
        raise NotFound("Credential is missing. Please authenticate again.") from exc
    except CredentialSyncError as exc:
        raise UpstreamFailed("Credential store is unavailable") from exc
    return JSONResponse({"version": version})


def _runtime_credential_url(conversation_id: UUID, secret_name: str) -> str:
    """Return the outer broker URL as seen from a conversation container."""
    parsed = urlsplit(os.getenv(_INTERNAL_SERVER_URL_ENV, _DEFAULT_INTERNAL_SERVER_URL))
    hostname = parsed.hostname
    if hostname in _LOOPBACK_HOSTS:
        hostname = "host.docker.internal"
    if hostname is None:
        raise ValueError("OH_INTERNAL_SERVER_URL must be an absolute URL")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname + (f":{parsed.port}" if parsed.port is not None else "")
    path = (
        f"/api/conversations/{conversation_id}"
        f"/runtime-credential-bindings/{secret_name}"
    )
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _persisted_codex_binding_state(
    registry: DockerConversationRegistry, conversation_id: UUID
) -> tuple[bool, bool, bool]:
    """Return (is Codex, requires binding, profile allows binding)."""
    conversation_dir = registry.conversation_dir(conversation_id)
    context = cipher_context(registry.resolve_persisted_cipher(conversation_id))
    try:
        stored = StoredConversation.model_validate_json(
            (conversation_dir / "meta.json").read_text(), context=context
        )
    except (OSError, ValueError):
        # A failed first start can leave a provisioning identity without a
        # complete conversation snapshot. The inner start remains authoritative.
        return False, False, True
    profile_allows = (
        stored.launched_agent_profile is None
        or stored.launched_agent_profile.allows_secret(CODEX_AUTH_SECRET_NAME)
    )
    required = CODEX_AUTH_SECRET_NAME in stored.required_runtime_credential_bindings
    state_file = conversation_dir / BASE_STATE
    if not state_file.is_file():
        return False, required, profile_allows
    try:
        state = ConversationState.model_validate_json(
            state_file.read_text(), context=context
        )
    except (OSError, ValueError):
        return False, required, profile_allows
    is_codex = isinstance(state.agent, ACPAgent) and state.agent.acp_server == "codex"
    return is_codex, required, profile_allows


async def _activate_runtime_credential_bindings(
    client: httpx.AsyncClient,
    container: ConversationContainer,
    conversation_id: UUID,
    secret_names: set[str],
) -> httpx.Response | None:
    for secret_name in sorted(secret_names):
        response = await client.put(
            f"{container.host}/api/conversations/{conversation_id}"
            f"/credential-bindings/{secret_name}",
            headers={"X-Session-API-Key": container.api_key},
            json={
                "url": _runtime_credential_url(conversation_id, secret_name),
                "headers": {"X-Session-API-Key": container.api_key},
            },
        )
        if response.is_error:
            logger.warning(
                "docker_credential_binding_activation",
                extra={
                    "credential": secret_name,
                    "outcome": "rejected",
                    "status_code": response.status_code,
                },
            )
            return response
        logger.info(
            "docker_credential_binding_activation",
            extra={"credential": secret_name, "outcome": "success"},
        )
    return None


async def _container(
    registry: DockerConversationRegistry, conversation_id: UUID
) -> ConversationContainer:
    if not registry.provisioning.manifest_path(conversation_id).is_file():
        raise NotFound("Conversation not found")
    try:
        return await registry.get_or_create(conversation_id)
    except RuntimeArchivedError as exc:
        raise Gone(
            "Conversation runtime was archived; its history is read-only"
        ) from exc
    except Exception as exc:
        logger.exception("Could not start conversation container %s", conversation_id)
        raise UpstreamFailed("Could not start conversation container") from exc


def _upstream_path(request: Request, path: str) -> str:
    query = strip_auth_query("?" + request.url.query).lstrip("?")
    return f"{path}?{query}" if query else path


async def _proxy_with_session(
    registry: DockerConversationRegistry,
    conversation_id: UUID,
    container: ConversationContainer,
    request: Request,
    *,
    upstream_path: str,
    body: bytes | None = None,
) -> StreamingResponse:
    """Proxy one request while holding an outer session attachment.

    The attachment is released when the streamed response finishes (or the
    client disconnects), so a long-lived proxied stream keeps the container
    from being evicted while a client is still reading from it.
    """
    registry.attach_session(conversation_id)
    release = registry.acquire_lease(conversation_id)

    detached = False

    async def detach() -> None:
        nonlocal detached
        if not detached:
            detached = True
            release()
            registry.detach_session(conversation_id)

    try:
        return await proxy_http(
            request,
            container,
            upstream_path=upstream_path,
            body=body,
            on_close=detach,
        )
    except BaseException:
        await detach()
        raise


docker_conversation_router = APIRouter(
    prefix="/conversations", tags=["Docker Conversations"]
)
# Register the exact broker routes before the conversation catch-all below.
docker_conversation_router.include_router(runtime_credential_broker_router)


def _worktree_git_paths(workspace_path: Path) -> dict[str, Path]:
    """The repository and git common dir a worktree start records, if any."""
    try:
        validate_git_repository(workspace_path)
        repo_root = Path(
            run_git_command(
                ["git", "--no-pager", "rev-parse", "--show-toplevel"],
                workspace_path,
            )
        ).resolve()
        git_common_dir = Path(
            run_git_command(
                ["git", "--no-pager", "rev-parse", "--git-common-dir"],
                workspace_path,
            )
        )
    except (GitCommandError, GitRepositoryError):
        return {}
    if not git_common_dir.is_absolute():
        git_common_dir = workspace_path / git_common_dir
    return {
        "worktree_repository_path": repo_root,
        "worktree_git_common_dir_path": git_common_dir.resolve(),
    }


@docker_conversation_router.post("")
async def start_conversation(
    request: Request,
    include_skills: Annotated[bool, Query()] = False,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> JSONResponse:
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Invalid("Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise Unprocessable("Expected a JSON object")
    status_code, content, conversation_id = await start_docker_conversation(
        body,
        get_registry(conversation_registry),
        include_skills=include_skills,
    )
    if status_code < 400:
        await conversation_service.refresh_persisted_conversation(conversation_id)
    return JSONResponse(content=content, status_code=status_code)


async def start_docker_conversation(
    body: dict[str, Any],
    registry: DockerConversationRegistry,
    *,
    include_skills: bool = False,
) -> tuple[int, Any, UUID]:
    """Start a Docker conversation through the same path as the REST route."""
    body = body.copy()
    if body.pop("conversation_runtime", None) not in (None, "docker"):
        raise Unprocessable("This server only runs docker conversations")

    workspace = body.get("workspace")
    if workspace is not None and (
        not isinstance(workspace, dict)
        or workspace.get("kind", "LocalWorkspace") != "LocalWorkspace"
    ):
        raise Unprocessable("Docker conversations require a local workspace")
    working_dir = (workspace or {}).get("working_dir", "/workspace")
    host_workspace = None if working_dir == "/workspace" else Path(working_dir)

    try:
        conversation_id = (
            UUID(body["conversation_id"]) if body.get("conversation_id") else uuid4()
        )
    except (TypeError, ValueError) as exc:
        raise Invalid("Invalid conversation_id") from exc
    body["conversation_id"] = str(conversation_id)
    body["workspace"] = {"kind": "LocalWorkspace", "working_dir": "/workspace"}

    # Only a conversation the container already created is left alone on
    # failure; a manifest from a failed earlier start is retried like a new one.
    try:
        existing = (
            registry.conversation_dir(conversation_id).joinpath("meta.json").is_file()
        )
    except ValueError as exc:
        raise Unprocessable(str(exc)) from exc
    release = None
    try:
        (
            payload,
            launched,
            options,
            worktree,
            credential_bindings,
        ) = await _prepare_forward(body, registry, existing=existing)
        identity = await run_store(
            registry.provisioning.create, conversation_id, host_workspace
        )
        updates: dict[str, Any] = {}
        if worktree:
            updates.update(
                await asyncio.to_thread(_worktree_git_paths, identity.workspace_path)
            )
        if launched is not None and identity.launched_agent_profile is None:
            updates["launched_agent_profile"] = launched
        # Fixed once the container accepted the conversation; until then a
        # retried start may change them.
        if not existing and identity.container != options:
            updates["container"] = options
        if updates:
            identity = identity.model_copy(update=updates)
            await run_store(registry.provisioning.save, identity)
        container = await registry.get_or_create(conversation_id)
        release = registry.acquire_lease(conversation_id)
        forwarded = await asyncio.to_thread(payload, identity.cipher)
        if worktree and identity.worktree_repository_path is not None:
            forwarded["workspace"] = LocalWorkspace(
                working_dir=identity.workspace_path
            ).model_dump(mode="json")
        async with httpx.AsyncClient(timeout=60) as client:
            activation_error = await _activate_runtime_credential_bindings(
                client,
                container,
                conversation_id,
                credential_bindings,
            )
            if (
                existing
                and activation_error is not None
                and activation_error.status_code == 409
            ):
                release()
                release = None
                await registry.stop(conversation_id)
                container = await registry.get_or_create(conversation_id)
                release = registry.acquire_lease(conversation_id)
                activation_error = await _activate_runtime_credential_bindings(
                    client,
                    container,
                    conversation_id,
                    credential_bindings,
                )
            if activation_error is not None:
                if activation_error.status_code in {404, 422}:
                    raise Unprocessable(
                        "ChatGPT credential is unavailable. Please sign in again."
                    )
                activation_error.raise_for_status()
            response = await client.post(
                f"{container.host}/api/conversations",
                params={"include_skills": include_skills},
                headers={"X-Session-API-Key": container.api_key},
                json=forwarded,
            )
    except (ProfileNotFound, AgentLaunchError, LaunchStoreError) as exc:
        raise launch_error(exc) from exc
    except ValueError as exc:
        raise Unprocessable(str(exc)) from exc
    except DomainError:
        if not existing:
            await registry.stop(conversation_id)
        raise
    except httpx.HTTPError as exc:
        if not existing:
            await registry.stop(conversation_id)
        raise UpstreamFailed(
            "Conversation container did not accept the request"
        ) from exc
    except Exception as exc:
        if not existing:
            await registry.stop(conversation_id)
        logger.exception("Could not create conversation container")
        raise UpstreamFailed("Could not create conversation container") from exc
    finally:
        if release is not None:
            release()

    content = response.json() if response.content else None
    if response.is_error:
        if not existing:
            await registry.stop(conversation_id)
        content = {"detail": "Conversation runtime rejected the request"}
    return response.status_code, content, conversation_id


async def _prepare_forward(
    body: dict[str, Any], registry: DockerConversationRegistry, *, existing: bool
) -> tuple[
    Callable[[Cipher], dict[str, Any]],
    LaunchedAgentProfile | None,
    ConversationContainerOptions | None,
    bool,
    set[str],
]:
    """Resolve the start request with this host's stores; the container finalizes.

    The profile of a conversation that already exists is not resolved again:
    its container returns the conversation as it is.
    """
    config = registry.config
    start = StartConversationRequest.model_validate(
        body,
        context=(
            cipher_context(config.cipher) if body.get("secrets_encrypted") else None
        ),
    )
    try:
        settings = await run_store(registry.stores.settings.load)
    except (OSError, PermissionError):
        settings = None
    settings = settings or PersistedSettings()
    context = settings.agent_settings.agent_context
    load_memory = context is not None and context.load_memory
    secrets = await asyncio.to_thread(request_secrets, start, config.cipher)
    source = (
        None
        if existing and start.agent_profile_id is not None
        else await asyncio.to_thread(
            launch_source,
            start,
            lambda: server_launch_stores(settings, registry.stores, config.cipher),
            config.cipher,
        )
    )
    has_host_codex_credential = (
        await run_store(registry.stores.secrets.get_secret, CODEX_AUTH_SECRET_NAME)
        is not None
    )
    credential_bindings: set[str] = set()
    is_codex_candidate = False
    binding_required = False
    profile_allows = True
    if existing and start.conversation_id is not None:
        is_codex_candidate, binding_required, profile_allows = await asyncio.to_thread(
            _persisted_codex_binding_state, registry, start.conversation_id
        )
        if profile_allows and (
            binding_required or (is_codex_candidate and has_host_codex_credential)
        ):
            credential_bindings.add(CODEX_AUTH_SECRET_NAME)
    else:
        allowed_secrets = (
            source.allowed_secrets if isinstance(source, ResolvedLaunch) else None
        )
        is_codex_candidate = source is not None and is_codex_source(source)
        profile_allows = (
            allowed_secrets is None or CODEX_AUTH_SECRET_NAME in allowed_secrets
        )
        if is_codex_candidate and has_host_codex_credential and profile_allows:
            credential_bindings.add(CODEX_AUTH_SECRET_NAME)
    if is_codex_candidate or binding_required:
        if credential_bindings:
            reason = (
                "runtime_requirement"
                if binding_required
                else "host_credential_available"
            )
            outcome = "selected"
        elif not profile_allows:
            reason = "profile_excluded"
            outcome = "fallback"
        else:
            reason = "host_credential_missing"
            outcome = "fallback"
        logger.info(
            "docker_credential_binding_selection",
            extra={
                "credential": CODEX_AUTH_SECRET_NAME,
                "outcome": outcome,
                "reason": reason,
            },
        )
    for secret_name in credential_bindings:
        secrets.pop(secret_name, None)

    def payload(cipher: Cipher) -> dict[str, Any]:
        return forward_to_runtime(
            start, source, secrets, cipher, load_memory=load_memory
        )

    launched = source.profile if isinstance(source, ResolvedLaunch) else None
    return payload, launched, start.container, start.worktree, credential_bindings


@docker_conversation_router.get(
    "/{conversation_id}/runtime", response_model=ConversationRuntimeInfo
)
async def runtime_info(
    conversation_id: UUID,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> ConversationRuntimeInfo:
    registry = get_registry(conversation_registry)
    if not registry.conversation_dir(conversation_id).joinpath("meta.json").is_file():
        raise NotFound("Conversation not found")
    return registry.runtime_info(conversation_id)


@docker_conversation_router.post("/{conversation_id}/runtime/credentials")
async def runtime_credentials(
    conversation_id: UUID,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> JSONResponse:
    """Return the key for an authenticated client attached to this runtime."""
    registry = get_registry(conversation_registry)
    if not registry.conversation_dir(conversation_id).joinpath("meta.json").is_file():
        raise NotFound("Conversation not found")
    identity = await run_store(registry.provisioning.load, conversation_id)
    return JSONResponse({"session_api_key": identity.api_key.get_secret_value()})


@docker_conversation_router.delete("/{conversation_id}/runtime", status_code=204)
async def release_runtime(
    conversation_id: UUID,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Response:
    """Stop the container while retaining conversation and workspace state."""
    registry = get_registry(conversation_registry)
    if not registry.conversation_dir(conversation_id).joinpath("meta.json").is_file():
        raise NotFound("Conversation not found")
    try:
        await registry.stop(conversation_id)
        await conversation_service.refresh_persisted_conversation(conversation_id)
    except Exception as exc:
        logger.exception("Could not release conversation runtime %s", conversation_id)
        raise UpstreamFailed("Could not release conversation runtime") from exc
    return Response(status_code=204)


@docker_conversation_router.post(
    "/{conversation_id}/runtime/reprovision", response_model=ConversationRuntimeInfo
)
async def reprovision_runtime(
    conversation_id: UUID,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> ConversationRuntimeInfo:
    registry = get_registry(conversation_registry)
    await _container(registry, conversation_id)
    return ConversationRuntimeInfo(
        runtime_status=ConversationRuntimeStatus.AVAILABLE, can_resume=True
    )


@docker_conversation_router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Response:
    registry = get_registry(conversation_registry)
    if not registry.provisioning.manifest_path(conversation_id).is_file():
        raise NotFound("Conversation not found")

    if not await registry.begin_delete(conversation_id):
        raise Conflict("Conversation deletion is already in progress")

    # Stopping the container closes its conversation service. The outer server
    # owns the bind-mounted state and removes it after Docker has unmounted it;
    # asking the inner server to remove the mount root leaves that root in a
    # partially deleted state.
    try:
        await registry.stop(conversation_id)
        try:
            await asyncio.to_thread(registry.cleanup_worktree, conversation_id)
        except (OSError, subprocess.SubprocessError):
            logger.exception(
                "Could not clean up conversation worktree %s", conversation_id
            )
        runtime_dir = await run_store(
            registry.provisioning.runtime_dir, conversation_id
        )
        await asyncio.to_thread(safe_rmtree, runtime_dir)
        await asyncio.to_thread(safe_rmtree, registry.conversation_dir(conversation_id))
        registry.provisioning.manifest_path(conversation_id).unlink(missing_ok=True)
        registry.archived_marker(conversation_id).unlink(missing_ok=True)
        await conversation_service.refresh_persisted_conversation(conversation_id)
    finally:
        await registry.finish_delete(conversation_id)
    return Response(status_code=200)


@docker_conversation_router.api_route(
    "/{conversation_id}",
    methods=["POST", "PUT", "PATCH", "OPTIONS"],
)
async def proxy_conversation_root(
    conversation_id: UUID,
    request: Request,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> StreamingResponse:
    return await proxy_conversation(
        conversation_id, "", request, conversation_registry, conversation_service
    )


@docker_conversation_router.api_route(
    "/{conversation_id}/{tail:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_conversation(
    conversation_id: UUID,
    tail: str,
    request: Request,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> StreamingResponse:
    if tail.split("/", 1)[0] in {
        "credential-bindings",
        "fork",
        "switch_llm",
        "switch_profile",
    }:
        raise NotSupported("This operation is unavailable in Docker runtime mode")
    registry = get_registry(conversation_registry)
    # The runtime's server has no settings of its own to check: the user's
    # switches live here and are followed at once.
    if tail == "reactions" and request.method == "POST":
        await require_feature_enabled(
            registry.stores.settings,
            "conversation_reactions",
            "Conversation reactions are turned off",
        )
    if tail == "ask_user/answer" and request.method == "POST":
        await require_feature_enabled(
            registry.stores.settings, "ask_user", "Decision cards are turned off"
        )
    container = await _container(registry, conversation_id)
    upstream_path = _upstream_path(
        request,
        f"/api/conversations/{conversation_id}" + (f"/{tail}" if tail else ""),
    )
    if tail == "secrets" and request.method == "POST":
        try:
            update = UpdateSecretsRequest.model_validate_json(await request.body())
        except ValueError as exc:
            raise Unprocessable("Invalid secrets payload") from exc
        identity = await run_store(registry.provisioning.load, conversation_id)
        profile = identity.launched_agent_profile
        secrets = update.secrets
        if profile is not None:
            secrets = {
                name: source
                for name, source in secrets.items()
                if profile.allows_secret(name)
            }
        materialized = await asyncio.to_thread(materialize_secrets, secrets)
        body = UpdateSecretsRequest(secrets=materialized).model_dump(
            mode="json", context=client_context("plaintext")
        )
        response = await _proxy_with_session(
            registry,
            conversation_id,
            container,
            request,
            upstream_path=upstream_path,
            body=json.dumps(body).encode(),
        )
    else:
        response = await _proxy_with_session(
            registry,
            conversation_id,
            container,
            request,
            upstream_path=upstream_path,
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
        await conversation_service.refresh_persisted_conversation(conversation_id)
    return response


docker_workspace_router = APIRouter(prefix="/conversations", tags=["Docker Workspace"])


@docker_workspace_router.get("/{conversation_id}/workspace/{file_path:path}")
async def proxy_workspace_file(
    conversation_id: UUID,
    file_path: str,
    request: Request,
    conversation_registry: ConversationRegistry = Depends(get_conversation_registry),
) -> StreamingResponse:
    registry = get_registry(conversation_registry)
    container = await _container(registry, conversation_id)
    return await _proxy_with_session(
        registry,
        conversation_id,
        container,
        request,
        upstream_path=_upstream_path(
            request, f"/api/conversations/{conversation_id}/workspace/{file_path}"
        ),
    )


docker_sockets_router = APIRouter(prefix="/sockets", tags=["Docker WebSockets"])
docker_session_sockets_router = APIRouter(prefix="/sockets", tags=["Docker WebSockets"])


async def _proxy_socket(
    websocket: WebSocket,
    conversation_id: UUID,
    session_api_key: str | None,
    socket_name: str,
    config: Config,
    registry: ConversationRegistry,
    conversation_service: ConversationService,
) -> None:
    from openhands.agent_server.sockets import _accept_authenticated_websocket

    if not await _accept_authenticated_websocket(websocket, session_api_key, config):
        return
    if not isinstance(registry, DockerConversationRegistry):
        await websocket.close(code=1011)
        return
    try:
        container = await _container(registry, conversation_id)
    except DomainError as exc:
        await websocket.close(code=1008 if isinstance(exc, NotFound) else 1011)
        return
    release = registry.acquire_lease(conversation_id)
    query = strip_auth_query("?" + websocket.url.query).lstrip("?")
    path = f"/sockets/{socket_name}/{conversation_id}"
    # Hold the attachment for the whole bridge lifetime so the idle-eviction
    # loop cannot stop the container under a live events/session websocket.
    registry.attach_session(conversation_id)
    try:
        await bridge_websocket(
            websocket,
            container,
            upstream_path=f"{path}?{query}" if query else path,
        )
    finally:
        if release is not None:
            release()
        registry.detach_session(conversation_id)
        await conversation_service.refresh_persisted_conversation(conversation_id)


@docker_sockets_router.websocket("/events/{conversation_id}")
async def proxy_events(
    websocket: WebSocket,
    conversation_id: UUID,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    config: Config = Depends(get_config),
    registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> None:
    await _proxy_socket(
        websocket,
        conversation_id,
        session_api_key,
        "events",
        config,
        registry,
        conversation_service,
    )


@docker_session_sockets_router.websocket("/session/{conversation_id}")
async def proxy_session(
    websocket: WebSocket,
    conversation_id: UUID,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    config: Config = Depends(get_config),
    registry: ConversationRegistry = Depends(get_conversation_registry),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> None:
    await _proxy_socket(
        websocket,
        conversation_id,
        session_api_key,
        "session",
        config,
        registry,
        conversation_service,
    )
