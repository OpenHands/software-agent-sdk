import asyncio
import importlib
import json
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel

from openhands.agent_server.config import Config, WebhookSpec
from openhands.agent_server.conversation_lease import (
    DEFAULT_LEASE_TTL_SECONDS,
    ConversationLeaseHeldError,
)
from openhands.agent_server.event_service import (
    LEASE_RENEW_INTERVAL_SECONDS,
    EventService,
)
from openhands.agent_server.models import (
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    LaunchedAgentProfile,
    StartConversationRequest,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.pub_sub import Subscriber
from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.agent_server.skills_service import discover_profile_skills_if_needed
from openhands.agent_server.utils import safe_rmtree, utc_now
from openhands.sdk import LLM, AgentContext, Event, Message
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.title_utils import (
    extract_message_text,
    generate_title_from_message,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.git.utils import run_git_command, validate_git_repository
from openhands.sdk.tool.client_tool import register_client_tools
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


if TYPE_CHECKING:
    from openhands.sdk.subagent.schema import AgentDefinition

CONVERSATION_WORKTREE_ROOT = Path("/tmp/conversation-worktrees")


def _build_worktree_guidance(
    *,
    source_workspace: Path,
    worktree_root: Path,
    workspace_dir: Path,
    branch: str,
) -> str:
    return (
        "This conversation uses a dedicated git worktree.\n"
        f"- Original workspace: {source_workspace}\n"
        f"- Worktree root: {worktree_root}\n"
        f"- Active workspace: {workspace_dir}\n"
        f"- Branch: {branch}\n"
        "Do all file and git work inside this worktree. Do your work on a new, "
        "appropriately-named branch, based off the main/master branch, "
        "and do not switch back to the original workspace."
    )


def _append_worktree_guidance(
    agent: AgentBase,
    *,
    source_workspace: Path,
    worktree_root: Path,
    workspace_dir: Path,
    branch: str,
) -> AgentBase:
    context = agent.agent_context or AgentContext()
    guidance = _build_worktree_guidance(
        source_workspace=source_workspace,
        worktree_root=worktree_root,
        workspace_dir=workspace_dir,
        branch=branch,
    )
    existing_suffix = (context.system_message_suffix or "").strip()
    suffix = f"{existing_suffix}\n\n{guidance}" if existing_suffix else guidance
    updated_context = context.model_copy(update={"system_message_suffix": suffix})
    return agent.model_copy(update={"agent_context": updated_context})


def _has_git_remote(repo_root: Path, remote: str = "origin") -> bool:
    try:
        run_git_command(["git", "remote", "get-url", remote], repo_root)
    except GitCommandError:
        return False
    return True


def _local_branch_exists(repo_root: Path, branch: str) -> bool:
    try:
        run_git_command(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            repo_root,
        )
    except GitCommandError:
        return False
    return True


def _get_worktree_start_point(repo_root: Path) -> str:
    """Resolve the base ref a new conversation worktree should be created from.

    Policy (in order):
      1. ``origin/<default_branch>`` if an ``origin`` remote is configured.
         ``git fetch origin`` is run first so the worktree starts from the
         latest remote tip; the default branch is resolved via
         ``refs/remotes/origin/HEAD``.
      2. Local ``main`` if there is no usable remote default but ``main``
         exists locally.
      3. Local ``master`` if neither remote default nor local ``main`` is
         available.
      4. Fall back to ``HEAD`` only when none of the above applies, so worktree
         creation still succeeds on freshly initialized repos.
    """
    if _has_git_remote(repo_root):
        try:
            run_git_command(["git", "fetch", "origin"], repo_root, timeout=60)
        except GitCommandError as exc:
            logger.warning(
                "git fetch origin failed while choosing worktree start point "
                "for %s; using cached refs. Error: %s",
                repo_root,
                exc,
            )
        try:
            ref = run_git_command(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                repo_root,
            )
        except GitCommandError:
            ref = ""
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return f"origin/{ref[len(prefix) :]}"

    if _local_branch_exists(repo_root, "main"):
        return "main"
    if _local_branch_exists(repo_root, "master"):
        return "master"
    return "HEAD"


def _create_conversation_worktree(
    workspace: LocalWorkspace,
    conversation_id: UUID,
) -> tuple[LocalWorkspace, Path, Path, str] | None:
    source_workspace = Path(workspace.working_dir).resolve()
    try:
        validate_git_repository(source_workspace)
        repo_root = Path(
            run_git_command(
                ["git", "--no-pager", "rev-parse", "--show-toplevel"],
                source_workspace,
            )
        ).resolve()
    except (GitCommandError, GitRepositoryError):
        return None

    relative_workspace = source_workspace.relative_to(repo_root)
    conversation_worktree_root = CONVERSATION_WORKTREE_ROOT / str(conversation_id)
    worktree_root = conversation_worktree_root / repo_root.name
    conversation_worktree_root.mkdir(parents=True, exist_ok=True)
    branch = f"openhands/{conversation_id}"

    if worktree_root.exists():
        try:
            run_git_command(
                ["git", "worktree", "remove", "--force", str(worktree_root)],
                repo_root,
            )
        except GitCommandError:
            safe_rmtree(worktree_root)

    run_git_command(["git", "worktree", "prune"], repo_root)

    if run_git_command(["git", "branch", "--list", branch], repo_root):
        run_git_command(["git", "branch", "-D", branch], repo_root)

    run_git_command(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_root),
            _get_worktree_start_point(repo_root),
        ],
        repo_root,
    )

    workspace_dir = worktree_root / relative_workspace
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return (
        LocalWorkspace(working_dir=workspace_dir),
        source_workspace,
        worktree_root,
        branch,
    )


def _prepare_request_workspace(
    request: StartConversationRequest,
    conversation_id: UUID,
) -> StartConversationRequest:
    if not request.worktree:
        return request

    worktree = _create_conversation_worktree(request.workspace, conversation_id)
    if worktree is None:
        return request

    new_workspace, source_workspace, worktree_root, branch = worktree
    assert request.agent is not None
    agent = _append_worktree_guidance(
        request.agent,
        source_workspace=source_workspace,
        worktree_root=worktree_root,
        workspace_dir=Path(new_workspace.working_dir),
        branch=branch,
    )
    return request.model_copy(update={"workspace": new_workspace, "agent": agent})


logger = logging.getLogger(__name__)


def _resolve_agent_from_profile(
    profile_id: "UUID",
    cipher: "Cipher | None",
    mcp_config: "Any",
) -> "tuple[AgentBase, LaunchedAgentProfile]":
    """Load and resolve an agent profile by id, returning the built agent + provenance.

    Runs synchronously (call via ``asyncio.to_thread`` from async context).

    Args:
        mcp_config: Global MCP config already loaded by the caller using the
            server's cipher.  Passed explicitly so this free function never
            touches the settings-store singleton (which may not have been
            initialised with the correct cipher yet).

    Raises:
        ProfileNotFound: No stored profile has ``profile_id``.
        DanglingMcpServerRef: A referenced MCP server is absent from the global config.
        DanglingSkillRef: A referenced skill is absent from the discovered catalog.
        ValueError: Profile load or settings validation failure.
    """
    from openhands.agent_server.persistence.store import (
        get_agent_profile_store,
        get_llm_profile_store,
    )
    from openhands.sdk.profiles.resolver import ProfileNotFound, resolve_agent_profile

    store = get_agent_profile_store()
    profile_name = store.name_for_id(profile_id)
    if profile_name is None:
        raise ProfileNotFound(f"Agent profile with id '{profile_id}' not found")

    try:
        profile = store.load(profile_name, cipher=cipher)
    except FileNotFoundError:
        raise ProfileNotFound(
            f"Agent profile '{profile_name}' (id={profile_id}) not found"
        )
    except ValueError as exc:
        raise ValueError(
            f"Failed to load agent profile '{profile_name}': {exc}"
        ) from exc

    # Both variants honor ``skill_refs``; the helper skips discovery when it
    # selects none. A genuine discovery failure fails the launch loudly rather
    # than silently producing a zero-skill agent.
    try:
        available_skills = discover_profile_skills_if_needed(profile)
    except Exception as exc:
        raise ValueError(
            f"Skill discovery failed for profile '{profile_name}': {exc}"
        ) from exc

    llm_store = get_llm_profile_store()
    try:
        settings_config = resolve_agent_profile(
            profile,
            llm_store=llm_store,
            mcp_config=mcp_config,
            available_skills=available_skills,
            cipher=cipher,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Profile '{profile_name}' failed to resolve: {exc}") from exc

    agent = settings_config.create_agent()
    launched = LaunchedAgentProfile(
        agent_profile_id=profile.id,
        revision=profile.revision,
    )
    return agent, launched


def _compose_conversation_info(
    stored: StoredConversation, state: ConversationState
) -> ConversationInfo:
    # Use mode='json' so SecretStr in nested structures (e.g. LookupSecret.headers,
    # agent.agent_context.secrets) serialize to strings. Without it, validation
    # fails because ConversationInfo expects dict[str, str] but receives SecretStr.
    #
    # ACP model state is lifted onto top-level ConversationInfo fields because
    # the agent holds it in PrivateAttrs (ACPAgent is frozen) which don't survive
    # ``model_dump``. ``getattr`` keeps non-ACP agents a no-op. We read the live
    # agent (fresh within a session) and fall back to ``state.agent_state`` —
    # persisted to ``base_state.json`` by ``ACPAgent._init`` (and kept in sync by
    # ``switch_acp_model``) — so cold list reads, where PrivateAttrs are still
    # empty because ``init_state`` hasn't fired, still surface the last-known
    # state. Persisted ``acp_available_models`` is a list of dicts that
    # ``ConversationInfo`` coerces back into ``ACPModelInfo``.
    agent_state = getattr(state, "agent_state", {}) or {}
    agent = state.agent
    # current_model_id: live PrivateAttr (fresh after a runtime switch) → the
    # persisted hint → the authoritative ``acp_model`` the agent runs on resume.
    #
    # The ``acp_model`` fallback is gated on the agent NOT being a live,
    # initialized one. Once ``init_state`` has fired, ``current_model_id`` is the
    # authoritative resolved value — including ``None`` when an override couldn't
    # be applied (unknown provider, or a resume whose model-selection call the
    # server rejected) — so falling back to ``acp_model`` there would re-assert an
    # override the live session isn't actually running. The fallback is only for
    # *cold* reads (``init_state`` hasn't fired, PrivateAttrs still empty), where
    # the serialized ``acp_model`` is the best last-known hint. The persisted
    # ``acp_current_model_id`` hint is kept honest by ``ACPAgent.init_state`` (it
    # clears the value whenever the override wasn't applied), so it's safe in
    # both cases.
    agent_initialized = bool(getattr(agent, "_initialized", False))
    current_model_id = (
        getattr(agent, "current_model_id", None)
        or agent_state.get("acp_current_model_id")
        or (None if agent_initialized else getattr(agent, "acp_model", None))
    )
    # available_models: the property returns ``[]`` (never ``None``) for *both* a
    # cold-read agent (PrivateAttr default, init_state hasn't fired) and a live
    # agent that genuinely has no models, so an ``is None`` check can't tell them
    # apart — and would drop the persisted picker payload on every cold list
    # read. The ``or`` chain is deliberate: an empty live list falls back to the
    # persisted snapshot, which is exactly right on cold reads (surface the
    # last-known list) and benign for a live empty session (the persisted value
    # is itself empty/absent there).
    available_models = (
        getattr(agent, "available_models", None)
        or agent_state.get("acp_available_models")
        or []
    )
    # Static provider capability. Unlike the two fields above it has no
    # meaningful live-vs-persisted distinction — it's derived from the stable
    # provider identity and written once at session init — so we read the
    # persisted value directly. Defaults False for non-ACP agents and
    # conversations that haven't started a session.
    supports_runtime_model_switch = bool(
        agent_state.get("acp_supports_runtime_model_switch", False)
    )
    return ConversationInfo(
        **state.model_dump(mode="json"),
        title=stored.title,
        metrics=stored.metrics,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        forked_from_conversation_id=stored.forked_from_conversation_id,
        forked_from_event_id=stored.forked_from_event_id,
        current_model_id=current_model_id,
        available_models=available_models,
        supports_runtime_model_switch=supports_runtime_model_switch,
        client_tools=stored.client_tools,
        launched_agent_profile=stored.launched_agent_profile,
    )


def _compose_webhook_conversation_info(
    stored: StoredConversation, state: ConversationState
) -> ConversationInfo:
    return _compose_conversation_info(stored, state)


def _update_state_tags_sync(
    state: ConversationState, tags: dict[str, str]
) -> ConversationState:
    with state:
        state.tags = tags
    return state


def _compose_webhook_conversation_info_sync(
    stored: StoredConversation, state: ConversationState
) -> ConversationInfo:
    with state:
        return _compose_webhook_conversation_info(stored, state)


def _register_agent_definitions(
    agent_defs: list["AgentDefinition"],
    *,
    context: str,
) -> None:
    """Register agent definitions into the subagent registry.

    Used both when creating new conversations (definitions forwarded from the
    client) and when resuming persisted ones (definitions stored in meta.json).
    """
    from openhands.sdk.subagent.registry import (
        agent_definition_to_factory,
        register_agent_if_absent,
    )

    registered = 0
    for agent_def in agent_defs:
        try:
            factory = agent_definition_to_factory(agent_def)
            register_agent_if_absent(
                name=agent_def.name,
                factory_func=factory,
                description=agent_def,
            )
            registered += 1
        except Exception as e:
            logger.warning(
                f"Failed to register agent definition "
                f"'{agent_def.name}' ({context}): {e}"
            )
    logger.debug(
        f"Registered {registered}/{len(agent_defs)} agent definition(s) ({context})"
    )


@dataclass
class ConversationService:
    """Conversation service backed by persisted local conversation state.

    Persisted conversations are discovered lazily: __aenter__ does not hydrate
    EventService instances from disk. They are loaded on demand via
    _get_or_load_event_service the first time a request needs to run them.
    """

    conversations_dir: Path = field()
    webhook_specs: list[WebhookSpec] = field(default_factory=list)
    session_api_key: str | None = field(default=None)
    cipher: Cipher | None = None
    owner_instance_id: str = field(default_factory=lambda: uuid4().hex)
    max_concurrent_runs: int = 10
    lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS
    _event_services: dict[UUID, EventService] | None = field(default=None, init=False)
    _stored_conversations: dict[UUID, StoredConversation] = field(
        default_factory=dict, init=False
    )
    _stored_conversations_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False
    )
    _conversation_webhook_subscribers: list["ConversationWebhookSubscriber"] = field(
        default_factory=list, init=False
    )
    _lease_renewal_task: asyncio.Task | None = field(default=None, init=False)
    _run_executor: ThreadPoolExecutor | None = field(default=None, init=False)

    def _conversation_dir(self, conversation_id: UUID) -> Path:
        return self.conversations_dir / conversation_id.hex

    def _meta_path(self, conversation_id: UUID) -> Path:
        return self._conversation_dir(conversation_id) / "meta.json"

    def _base_state_path(self, conversation_id: UUID) -> Path:
        return self._conversation_dir(conversation_id) / BASE_STATE

    def _load_stored_conversation_from_meta(
        self, meta_file: Path
    ) -> StoredConversation:
        return StoredConversation.model_validate_json(
            meta_file.read_text(),
            context={"cipher": self.cipher},
        )

    def _event_service_stored_if_open(
        self, event_service: EventService
    ) -> StoredConversation | None:
        conversation = getattr(event_service, "__dict__", {}).get("_conversation")
        if conversation is not None:
            return event_service.stored

        is_open = getattr(event_service, "is_open", None)
        if callable(is_open) and not is_open():
            return None
        return event_service.stored

    async def _compose_active_conversation_info(
        self, event_service: EventService
    ) -> ConversationInfo | None:
        conversation = getattr(event_service, "__dict__", {}).get("_conversation")
        if conversation is not None:
            return _compose_conversation_info(event_service.stored, conversation._state)

        if self._event_service_stored_if_open(event_service) is None:
            return None
        try:
            state = await event_service.get_state()
        except ValueError:
            return None
        return _compose_conversation_info(event_service.stored, state)

    def _load_stored_conversation_locked(
        self, conversation_id: UUID
    ) -> StoredConversation | None:
        stored = self._stored_conversations.get(conversation_id)
        if stored is not None:
            return stored

        meta_file = self._meta_path(conversation_id)
        if not meta_file.exists():
            return None

        stored = self._load_stored_conversation_from_meta(meta_file)
        self._stored_conversations[stored.id] = stored
        return stored

    async def _load_stored_conversation(
        self, conversation_id: UUID
    ) -> StoredConversation | None:
        event_services = self._event_services
        if event_services is not None:
            event_service = event_services.get(conversation_id)
            if event_service is not None:
                stored = self._event_service_stored_if_open(event_service)
                if stored is not None:
                    return stored

        async with self._stored_conversations_lock:
            return self._load_stored_conversation_locked(conversation_id)

    async def _load_all_stored_conversations(self) -> dict[UUID, StoredConversation]:
        async with self._stored_conversations_lock:
            if self.conversations_dir.exists():
                for conversation_dir in self.conversations_dir.iterdir():
                    meta_file = conversation_dir / "meta.json"
                    if not meta_file.exists():
                        continue
                    try:
                        conversation_id = UUID(hex=conversation_dir.name)
                    except ValueError:
                        conversation_id = None
                    if (
                        conversation_id is not None
                        and conversation_id in self._stored_conversations
                    ):
                        continue
                    try:
                        stored = self._load_stored_conversation_from_meta(meta_file)
                    except Exception:
                        logger.exception(
                            "error_loading_conversation_meta:%s",
                            conversation_dir,
                            stack_info=True,
                        )
                        continue
                    self._stored_conversations[stored.id] = stored
            return dict(self._stored_conversations)

    async def _cache_stored_conversation(self, stored: StoredConversation) -> None:
        async with self._stored_conversations_lock:
            self._stored_conversations[stored.id] = stored

    async def _forget_stored_conversation(self, conversation_id: UUID) -> None:
        async with self._stored_conversations_lock:
            self._stored_conversations.pop(conversation_id, None)

    def _prepare_persisted_conversation_runtime(
        self, stored: StoredConversation, *, context: str
    ) -> None:
        if stored.tool_module_qualnames:
            for tool_name, module_qualname in stored.tool_module_qualnames.items():
                try:
                    importlib.import_module(module_qualname)
                    logger.debug(
                        "Tool '%s' registered via module '%s' while %s",
                        tool_name,
                        module_qualname,
                        context,
                    )
                except ImportError as exc:
                    logger.warning(
                        "Failed to import module '%s' for tool '%s' while %s: %s. "
                        "Tool will not be available.",
                        module_qualname,
                        tool_name,
                        context,
                        exc,
                    )
            logger.debug(
                "Dynamically registered %d tools while %s: %s",
                len(stored.tool_module_qualnames),
                context,
                list(stored.tool_module_qualnames.keys()),
            )

        if stored.client_tools:
            register_client_tools(stored.client_tools)

        if stored.agent_definitions:
            _register_agent_definitions(stored.agent_definitions, context=context)

    def _load_persisted_state(
        self, stored: StoredConversation
    ) -> ConversationState | None:
        base_state_file = self._base_state_path(stored.id)
        if not base_state_file.exists():
            return None

        state_context = {"cipher": self.cipher} if self.cipher else None
        return ConversationState.model_validate(
            json.loads(base_state_file.read_text()),
            context=state_context,
        )

    async def _get_or_load_event_service(
        self, conversation_id: UUID
    ) -> EventService | None:
        event_services = self._event_services
        if event_services is None:
            raise ValueError("inactive_service")

        event_service = event_services.get(conversation_id)
        if (
            event_service is not None
            and self._event_service_stored_if_open(event_service) is not None
        ):
            return event_service

        stored = await self._load_stored_conversation(conversation_id)
        if stored is None:
            return None

        self._prepare_persisted_conversation_runtime(
            stored,
            context=f"resuming conversation {stored.id}",
        )
        try:
            return await self._start_event_service(stored)
        except ConversationLeaseHeldError as exc:
            logger.debug(
                "Skipping active conversation %s owned by %s until %s",
                stored.id,
                exc.owner_instance_id,
                exc.expires_at,
            )
            return None

    async def _load_conversation_info(
        self, conversation_id: UUID
    ) -> ConversationInfo | None:
        event_services = self._event_services
        if event_services is None:
            raise ValueError("inactive_service")

        event_service = event_services.get(conversation_id)
        if event_service is not None:
            conversation_info = await self._compose_active_conversation_info(
                event_service
            )
            if conversation_info is not None:
                return conversation_info

        stored = await self._load_stored_conversation(conversation_id)
        if stored is None:
            return None
        state = self._load_persisted_state(stored)
        if state is None:
            return None
        return _compose_conversation_info(stored, state)

    async def get_conversation(self, conversation_id: UUID) -> ConversationInfo | None:
        return await self._load_conversation_info(conversation_id)

    async def get_acp_conversation(
        self, conversation_id: UUID
    ) -> ConversationInfo | None:
        return await self._load_conversation_info(conversation_id)

    async def search_conversations(
        self,
        page_id: str | None = None,
        limit: int = 100,
        execution_status: ConversationExecutionStatus | None = None,
        sort_order: ConversationSortOrder = ConversationSortOrder.CREATED_AT_DESC,
    ) -> ConversationPage:
        items, next_page_id = await self._search_conversations(
            page_id=page_id,
            limit=limit,
            execution_status=execution_status,
            sort_order=sort_order,
        )
        return ConversationPage(
            items=items,
            next_page_id=next_page_id,
        )

    async def search_acp_conversations(
        self,
        page_id: str | None = None,
        limit: int = 100,
        execution_status: ConversationExecutionStatus | None = None,
        sort_order: ConversationSortOrder = ConversationSortOrder.CREATED_AT_DESC,
    ) -> ConversationPage:
        items, next_page_id = await self._search_conversations(
            page_id=page_id,
            limit=limit,
            execution_status=execution_status,
            sort_order=sort_order,
        )
        return ConversationPage(
            items=items,
            next_page_id=next_page_id,
        )

    async def _conversation_records(
        self, event_services: dict[UUID, EventService]
    ) -> list[tuple[UUID, StoredConversation, EventService | None]]:
        records: dict[UUID, tuple[StoredConversation, EventService | None]] = {}

        for conversation_id, event_service in list(event_services.items()):
            stored = self._event_service_stored_if_open(event_service)
            if stored is not None:
                records[conversation_id] = (stored, event_service)

        for conversation_id, stored in (
            await self._load_all_stored_conversations()
        ).items():
            records.setdefault(conversation_id, (stored, None))

        return [
            (conversation_id, stored, event_service)
            for conversation_id, (stored, event_service) in records.items()
        ]

    def _sort_conversation_records(
        self,
        records: list[tuple[UUID, StoredConversation, EventService | None]],
        sort_order: ConversationSortOrder,
    ) -> list[tuple[UUID, StoredConversation, EventService | None]]:
        def created_at_key(
            record: tuple[UUID, StoredConversation, EventService | None],
        ):
            return record[1].created_at

        def updated_at_key(
            record: tuple[UUID, StoredConversation, EventService | None],
        ):
            return record[1].updated_at

        if sort_order in (
            ConversationSortOrder.CREATED_AT,
            ConversationSortOrder.CREATED_AT_DESC,
        ):
            key = created_at_key
        else:
            key = updated_at_key
        return sorted(
            records,
            key=key,
            reverse=sort_order
            in (
                ConversationSortOrder.CREATED_AT_DESC,
                ConversationSortOrder.UPDATED_AT_DESC,
            ),
        )

    async def _compose_conversation_info_from_record(
        self, stored: StoredConversation, event_service: EventService | None
    ) -> ConversationInfo | None:
        if event_service is not None:
            conversation_info = await self._compose_active_conversation_info(
                event_service
            )
            if conversation_info is not None:
                return conversation_info

        state = self._load_persisted_state(stored)
        if state is None:
            return None
        return _compose_conversation_info(stored, state)

    async def _search_conversations_from_page(
        self,
        records: list[tuple[UUID, StoredConversation, EventService | None]],
        page_id: str | None,
        limit: int,
        execution_status: ConversationExecutionStatus | None,
    ) -> tuple[list[ConversationInfo], str | None, bool]:
        items: list[ConversationInfo] = []
        next_page_id = None
        started = page_id is None
        found_page_id = page_id is None

        for conversation_id, stored, event_service in records:
            if execution_status is None and not started:
                if conversation_id.hex == page_id:
                    started = True
                    found_page_id = True
                else:
                    continue

            conversation_info = await self._compose_conversation_info_from_record(
                stored, event_service
            )
            if conversation_info is None:
                continue
            if (
                execution_status is not None
                and conversation_info.execution_status != execution_status
            ):
                continue

            if execution_status is not None and not started:
                if conversation_id.hex == page_id:
                    started = True
                    found_page_id = True
                else:
                    continue

            if len(items) >= limit:
                next_page_id = conversation_id.hex
                break
            items.append(conversation_info)

        return items, next_page_id, found_page_id

    async def _search_conversations(
        self,
        page_id: str | None,
        limit: int,
        execution_status: ConversationExecutionStatus | None,
        sort_order: ConversationSortOrder,
    ) -> tuple[list[ConversationInfo], str | None]:
        event_services = self._event_services
        if event_services is None:
            raise ValueError("inactive_service")

        records = self._sort_conversation_records(
            await self._conversation_records(event_services), sort_order
        )
        items, next_page_id, found_page_id = await self._search_conversations_from_page(
            records, page_id, limit, execution_status
        )
        if page_id is not None and not found_page_id:
            items, next_page_id, _ = await self._search_conversations_from_page(
                records, None, limit, execution_status
            )
        return items, next_page_id

    async def count_conversations(
        self,
        execution_status: ConversationExecutionStatus | None = None,
    ) -> int:
        return await self._count_conversations(execution_status=execution_status)

    async def _count_conversations(
        self,
        execution_status: ConversationExecutionStatus | None,
    ) -> int:
        """Count conversations matching the given filters."""
        event_services = self._event_services
        if event_services is None:
            raise ValueError("inactive_service")

        records = await self._conversation_records(event_services)
        if execution_status is None:
            return len(records)

        count = 0
        for _, stored, event_service in records:
            conversation_info = await self._compose_conversation_info_from_record(
                stored, event_service
            )
            if (
                conversation_info is not None
                and conversation_info.execution_status == execution_status
            ):
                count += 1
        return count

    async def batch_get_conversations(
        self, conversation_ids: list[UUID]
    ) -> list[ConversationInfo | None]:
        """Given a list of ids, get a batch of conversation info, returning
        None for any that were not found."""
        results = await asyncio.gather(
            *[
                self.get_conversation(conversation_id)
                for conversation_id in conversation_ids
            ]
        )
        return results

    async def batch_get_acp_conversations(
        self, conversation_ids: list[UUID]
    ) -> list[ConversationInfo | None]:
        results = await asyncio.gather(
            *[
                self.get_conversation(conversation_id)
                for conversation_id in conversation_ids
            ]
        )
        return results

    async def _notify_conversation_webhooks(self, conversation_info: BaseModel):
        """Notify all conversation webhook subscribers about conversation changes."""
        if not self._conversation_webhook_subscribers:
            return

        # Send notifications to all conversation webhook subscribers in the background
        async def _notify_and_log_errors():
            results = await asyncio.gather(
                *[
                    subscriber.post_conversation_info(conversation_info)
                    for subscriber in self._conversation_webhook_subscribers
                ],
                return_exceptions=True,  # Don't fail if one webhook fails
            )

            # Log any exceptions that occurred
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    subscriber = self._conversation_webhook_subscribers[i]
                    logger.error(
                        (
                            f"Failed to notify conversation webhook "
                            f"{subscriber.spec.base_url}: {result}"
                        ),
                        exc_info=result,
                    )

        # Create task to run in background without awaiting
        asyncio.create_task(_notify_and_log_errors())

    # Write Methods

    async def start_conversation(
        self, request: StartConversationRequest
    ) -> tuple[ConversationInfo, bool]:
        return await self._start_conversation(request)

    async def start_acp_conversation(
        self, request: StartConversationRequest
    ) -> tuple[ConversationInfo, bool]:
        return await self._start_conversation(request)

    async def _start_conversation(
        self,
        request: StartConversationRequest,
    ) -> tuple[ConversationInfo, bool]:
        """Start a local event_service and return its id."""
        if self._event_services is None:
            raise ValueError("inactive_service")
        conversation_id = request.conversation_id or uuid4()
        existing_conversation = await self._load_conversation_info(conversation_id)
        if existing_conversation is not None:
            return existing_conversation, False

        # Profile resolution must happen before _prepare_request_workspace (which
        # asserts request.agent is not None) and before model_dump so the resolved
        # agent is captured in request_data.
        launched_agent_profile: LaunchedAgentProfile | None = None
        if request.agent_profile_id is not None:
            # get_settings_store() is safe here: get_instance() initialises the
            # singleton with the server cipher before any conversation can start.
            from openhands.agent_server.persistence import (
                PersistedSettings,
                get_settings_store,
            )

            settings = get_settings_store().load() or PersistedSettings()
            mcp_config = settings.agent_settings.mcp_config
            resolved_agent, launched_agent_profile = await asyncio.to_thread(
                _resolve_agent_from_profile,
                request.agent_profile_id,
                self.cipher,
                mcp_config,
            )
            request = request.model_copy(update={"agent": resolved_agent})

        request = _prepare_request_workspace(request, conversation_id)

        # Dynamically register tools from client's registry
        if request.tool_module_qualnames:
            import importlib

            for tool_name, module_qualname in request.tool_module_qualnames.items():
                try:
                    # Import the module to trigger tool auto-registration
                    importlib.import_module(module_qualname)
                    logger.debug(
                        f"Tool '{tool_name}' registered via module '{module_qualname}'"
                    )
                except ImportError as e:
                    logger.warning(
                        f"Failed to import module '{module_qualname}' for tool "
                        f"'{tool_name}': {e}. Tool will not be available."
                    )
                    # Continue even if some tools fail to register
                    # The agent will fail gracefully if it tries to use unregistered
                    # tools
            if request.tool_module_qualnames:
                logger.info(
                    "Dynamically registered %d tools for conversation %s",
                    len(request.tool_module_qualnames),
                    conversation_id,
                )

        # Register client-defined tools (JSON specs, no Python code). The
        # ClientTool *class* is registered statelessly; each tool's schema
        # travels with the conversation via the returned Tool.params, so
        # concurrent conversations never clobber each other's schemas.
        if request.client_tools:
            client_tool_specs = register_client_tools(request.client_tools)
            # Inject Tool specs into the agent so _initialize() resolves them
            existing_names = {t.name for t in request.agent.tools}
            new_tools = [
                ts for ts in client_tool_specs if ts.name not in existing_names
            ]
            if new_tools:
                request.agent = request.agent.model_copy(
                    update={"tools": [*request.agent.tools, *new_tools]}
                )

        # Register subagent definitions forwarded from the client
        if request.agent_definitions:
            _register_agent_definitions(
                request.agent_definitions,
                context=f"conversation {conversation_id}",
            )

        # Plugin loading is now handled lazily by LocalConversation.
        # Just pass the plugin specs through to StoredConversation.
        # LocalConversation will:
        # 1. Fetch and load plugins on first run()/send_message()
        # 2. Resolve refs to commit SHAs for deterministic resume
        # 3. Merge plugin skills/MCP/hooks into the agent
        #
        # Use mode='json' so SecretStr in nested structures (e.g. LookupSecret.headers)
        # serialize to plain strings. Pass expose_secrets=True so StaticSecret values
        # are preserved through the round-trip; the dict is only used in-process to
        # construct StoredConversation, not sent over the network.
        # agent_profile_id is excluded: it was resolved into `launched_agent_profile`
        # above and must not re-trigger the mutual-exclusivity validator.
        request_data = request.model_dump(
            mode="json",
            context={"expose_secrets": True},
            exclude={"agent_profile_id"},
        )

        # If secrets_encrypted=True, the agent's secrets (e.g., LLM api_key) are
        # cipher-encrypted and need decryption during model validation. Pass the
        # cipher in the validation context so validate_secret() can decrypt them.
        if request.secrets_encrypted:
            if self.cipher is None:
                raise ValueError(
                    "Cannot decrypt secrets: cipher not configured. "
                    "Set OH_SECRET_KEY environment variable."
                )
            stored = StoredConversation.model_validate(
                {
                    "id": conversation_id,
                    **request_data,
                    "launched_agent_profile": (
                        launched_agent_profile.model_dump(mode="json")
                        if launched_agent_profile is not None
                        else None
                    ),
                },
                context={"cipher": self.cipher},
            )
        else:
            stored = StoredConversation(
                id=conversation_id,
                launched_agent_profile=launched_agent_profile,
                **request_data,
            )
        event_service = await self._start_event_service(stored)
        initial_message = request.initial_message
        if initial_message:
            message = Message(
                role=initial_message.role, content=initial_message.content
            )
            await event_service.send_message(message, True)

        state = await event_service.get_state()
        conversation_info = _compose_conversation_info(event_service.stored, state)

        # Notify conversation webhooks about the started conversation
        await self._notify_conversation_webhooks(
            _compose_webhook_conversation_info(event_service.stored, state)
        )

        return conversation_info, True

    async def pause_conversation(self, conversation_id: UUID) -> bool:
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service:
            await event_service.pause()
            state = await event_service.get_state()
            conversation_info = _compose_webhook_conversation_info(
                event_service.stored, state
            )
            await self._notify_conversation_webhooks(conversation_info)
        return bool(event_service)

    async def interrupt_conversation(self, conversation_id: UUID) -> bool:
        """Immediately cancel an in-flight LLM call for a conversation.

        Unlike :meth:`pause_conversation`, which waits for the current
        LLM request to finish, this cancels the running ``arun()`` task
        so the interruption takes effect mid-stream.
        """
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service:
            await event_service.interrupt()
            state = await event_service.get_state()
            conversation_info = _compose_webhook_conversation_info(
                event_service.stored, state
            )
            await self._notify_conversation_webhooks(conversation_info)
        return bool(event_service)

    async def resume_conversation(self, conversation_id: UUID) -> bool:
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service:
            await event_service.start()
        return bool(event_service)

    async def delete_conversation(self, conversation_id: UUID) -> bool:
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service is None:
            return False

        if self._event_services is None:
            raise ValueError("inactive_service")
        self._event_services.pop(conversation_id, None)
        await self._forget_stored_conversation(conversation_id)

        try:
            state = await event_service.get_state()
            conversation_info = _compose_webhook_conversation_info(
                event_service.stored, state
            )
            conversation_info.execution_status = ConversationExecutionStatus.DELETING
            await self._notify_conversation_webhooks(conversation_info)
        except Exception as exc:
            logger.warning(
                "Failed to notify webhooks for conversation %s: %s",
                conversation_id,
                exc,
            )

        try:
            await event_service.close()
        except Exception as exc:
            logger.warning(
                "Failed to close event service for conversation %s: %s",
                conversation_id,
                exc,
            )

        safe_rmtree(
            event_service.conversation_dir,
            f"conversation directory for {conversation_id}",
        )

        logger.info("Successfully deleted conversation %s", conversation_id)
        return True

    async def update_conversation(
        self, conversation_id: UUID, request: UpdateConversationRequest
    ) -> bool:
        """Update conversation metadata.

        Args:
            conversation_id: The ID of the conversation to update
            request: Request object containing fields to update (e.g., title, tags)

        Returns:
            bool: True if the conversation was updated successfully, False if not found
        """
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service is None:
            return False

        loop = asyncio.get_running_loop()
        state = await event_service.get_state()
        if request.title is not None:
            event_service.stored.title = request.title.strip()
        if request.tags is not None:
            event_service.stored.tags = request.tags
            # Keep the persisted ConversationState update under the state lock so
            # autosave and state-change callbacks observe a consistent mutation.
            state = await loop.run_in_executor(
                None, _update_state_tags_sync, state, request.tags
            )
        event_service.stored.updated_at = utc_now()
        # Save the updated metadata to disk
        await event_service.save_meta()

        # Notify conversation webhooks about the updated conversation. Compose the
        # full-state snapshot under the state lock, but do the synchronous wait in a
        # worker thread so metadata updates cannot block the FastAPI event loop.
        conversation_info = await loop.run_in_executor(
            None, _compose_webhook_conversation_info_sync, event_service.stored, state
        )
        await self._notify_conversation_webhooks(conversation_info)

        updated_fields = []
        if request.title is not None:
            updated_fields.append("title")
        if request.tags is not None:
            updated_fields.append("tags")
        logger.info(
            "Successfully updated conversation %s (%s)",
            conversation_id,
            ", ".join(updated_fields),
        )
        return True

    async def get_event_service(self, conversation_id: UUID) -> EventService | None:
        return await self._get_or_load_event_service(conversation_id)

    async def generate_conversation_title(
        self, conversation_id: UUID, max_length: int = 50, llm: LLM | None = None
    ) -> str | None:
        """Generate a title for the conversation using LLM."""
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service is None:
            return None

        title = await event_service.generate_title(llm=llm, max_length=max_length)
        return title

    async def ask_agent(self, conversation_id: UUID, question: str) -> str | None:
        """Ask the agent a simple question without affecting conversation state."""
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service is None:
            return None

        response = await event_service.ask_agent(question)
        return response

    async def condense(self, conversation_id: UUID) -> bool:
        """Force condensation of the conversation history."""
        event_service = await self._get_or_load_event_service(conversation_id)
        if event_service is None:
            return False

        await event_service.condense()
        return True

    async def fork_conversation(
        self,
        source_id: UUID,
        *,
        fork_id: UUID | None = None,
        title: str | None = None,
        tags: dict[str, str] | None = None,
        reset_metrics: bool = True,
        from_event_id: str | None = None,
    ) -> ConversationInfo | None:
        """Fork an existing conversation, deep-copying its event history.

        The fork is persisted to disk and then loaded as a new EventService,
        so the forked conversation is fully independent from the source.

        When *from_event_id* is set, only the branch up to and including that
        event is copied and the fork's HEAD is set there; otherwise the whole
        conversation is copied (today's behavior).

        Returns ``None`` when *source_id* does not exist.

        Raises:
            ValueError: If *fork_id* is already taken by an active
                conversation, or if *from_event_id* is not in the source
                conversation.
        """
        if self._event_services is None:
            raise ValueError("inactive_service")

        if fork_id is not None:
            existing_service = self._event_services.get(fork_id)
            if (
                existing_service is not None
                and self._event_service_stored_if_open(existing_service) is not None
            ):
                raise ValueError(f"Conversation with id {fork_id} already exists")

        source_service = await self._get_or_load_event_service(source_id)
        if source_service is None:
            return None

        source_conversation = source_service.get_conversation()

        # fork() deep-copies events, state, and writes to a new persistence dir.
        fork_conv = await asyncio.to_thread(
            source_conversation.fork,
            conversation_id=fork_id,
            title=title,
            tags=tags,
            reset_metrics=reset_metrics,
            from_event_id=from_event_id,
        )
        # Extract the persisted data, then discard the temporary conversation.
        fork_conv_id = fork_conv.id
        fork_agent = cast(AgentBase, fork_conv.agent)
        fork_workspace = fork_conv.workspace
        fork_conv.delete_on_close = False
        fork_conv.close()

        # _start_event_service will resume from the persisted fork directory.
        # Copy the source's stored metadata so request-level configuration
        # (client_tools, tool_module_qualnames, agent_definitions, plugins,
        # secrets, ...) is preserved on the fork, then override only the
        # fork-specific fields. Without this, e.g. a fork of a client-tool
        # conversation would lose ``client_tools`` in meta.json and be unable
        # to re-register its tools after a server restart.
        fork_overrides: dict[str, Any] = {
            "id": fork_conv_id,
            "agent": fork_agent,
            "workspace": fork_workspace,
            "title": title,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "forked_from_conversation_id": source_id,
            "forked_from_event_id": from_event_id,
        }
        if reset_metrics:
            fork_overrides["metrics"] = None
        if tags is not None:
            fork_overrides["tags"] = tags
        fork_stored = source_service.stored.model_copy(update=fork_overrides)
        # If the service fails to start, clean up the orphaned persistence
        # directory so we don't leave stale state on disk.
        fork_dir = self.conversations_dir / fork_conv_id.hex
        try:
            fork_event_service = await self._start_event_service(fork_stored)
        except Exception:
            safe_rmtree(fork_dir)
            raise

        state = await fork_event_service.get_state()
        return _compose_conversation_info(fork_event_service.stored, state)

    async def navigate_conversation(
        self, conversation_id: UUID, *, event_id: str | None = None
    ) -> ConversationInfo | None:
        """Move a conversation's HEAD to an existing event (in-place re-root).

        All branches stay on disk; only the active branch the agent runs on
        changes. The new HEAD persists via the conversation's own state
        autosave. Returns ``None`` when *conversation_id* does not exist.

        Raises:
            ValueError: If *event_id* is not ``None`` and not present in the
                conversation.
        """
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return None

        await event_service.navigate_to(event_id)
        state = await event_service.get_state()
        return _compose_conversation_info(event_service.stored, state)

    async def __aenter__(self):
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self._run_executor = ThreadPoolExecutor(
            max_workers=self.max_concurrent_runs,
            thread_name_prefix="conversation-run",
        )
        self._event_services = {}
        async with self._stored_conversations_lock:
            self._stored_conversations = {}

        # Initialize conversation webhook subscribers
        self._conversation_webhook_subscribers = [
            ConversationWebhookSubscriber(
                spec=webhook_spec,
                session_api_key=self.session_api_key,
            )
            for webhook_spec in self.webhook_specs
        ]

        self._lease_renewal_task = asyncio.create_task(self._renew_all_leases_loop())

        return self

    async def _renew_all_leases_loop(self) -> None:
        """Single background task that renews leases for all active conversations.

        Replaces N per-conversation renewal tasks with one centralized loop,
        reducing asyncio task overhead.  Each renewal involves synchronous
        file I/O (FileLock + read + write), so individual calls are offloaded
        via ``asyncio.to_thread`` to avoid blocking the event loop.
        """
        try:
            while True:
                await asyncio.sleep(LEASE_RENEW_INTERVAL_SECONDS)
                event_services = self._event_services
                if event_services is None:
                    return
                for event_service in list(event_services.values()):
                    await asyncio.to_thread(event_service.renew_lease)
        except asyncio.CancelledError:
            raise

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self._lease_renewal_task is not None:
            self._lease_renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._lease_renewal_task
            self._lease_renewal_task = None

        event_services = self._event_services
        if event_services is None:
            return
        self._event_services = None
        async with self._stored_conversations_lock:
            self._stored_conversations = {}
        # This stops conversations and saves meta
        await asyncio.gather(
            *[
                event_service.__aexit__(exc_type, exc_value, traceback)
                for event_service in event_services.values()
            ]
        )
        if self._run_executor is not None:
            self._run_executor.shutdown(wait=False)
            self._run_executor = None

    @classmethod
    def get_instance(cls, config: Config) -> "ConversationService":
        # Initialise the settings-store singleton with the server cipher before
        # any conversation handler can call get_settings_store() without config.
        from openhands.agent_server.persistence import get_settings_store

        get_settings_store(config)
        return ConversationService(
            conversations_dir=config.conversations_path,
            webhook_specs=config.webhooks,
            session_api_key=(
                config.session_api_keys[0] if config.session_api_keys else None
            ),
            cipher=config.cipher,
            max_concurrent_runs=config.max_concurrent_runs,
            lease_ttl_seconds=config.lease_ttl_seconds,
        )

    async def _start_event_service(self, stored: StoredConversation) -> EventService:
        event_services = self._event_services
        if event_services is None:
            raise ValueError("inactive_service")

        await self._cache_stored_conversation(stored)
        event_service = EventService(
            stored=stored,
            conversations_dir=self.conversations_dir,
            cipher=self.cipher,
            owner_instance_id=self.owner_instance_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        # Lease renewal is handled by the centralized
        # _renew_all_leases_loop task on ConversationService.
        event_service._external_lease_renewal = True
        event_service._run_executor = self._run_executor

        try:
            await event_service.start()
            # Register subscribers after start() so subscribe_to_events runs
            # its initial-state push synchronously and any failure surfaces to
            # the caller instead of being silently logged on a later publish.
            await event_service.subscribe_to_events(
                _EventSubscriber(service=event_service)
            )
            if stored.autotitle and stored.title is None:
                await event_service.subscribe_to_events(
                    AutoTitleSubscriber(service=event_service)
                )
            await asyncio.gather(
                *[
                    event_service.subscribe_to_events(
                        WebhookSubscriber(
                            conversation_id=stored.id,
                            service=event_service,
                            spec=webhook_spec,
                            session_api_key=self.session_api_key,
                        )
                    )
                    for webhook_spec in self.webhook_specs
                ]
            )
            # Save metadata immediately after successful start to ensure persistence
            # even if the system is not shut down gracefully
            await event_service.save_meta()
        except Exception:
            # Clean up the event service if startup fails
            await event_service.close()
            raise

        event_services[stored.id] = event_service
        return event_service


@dataclass
class _EventSubscriber(Subscriber):
    service: EventService

    async def __call__(self, _event: Event):
        # Skip updating timestamp for ConversationStateUpdateEvent, which is
        # published during startup/state changes and doesn't represent actual
        # conversation activity. This prevents updated_at from being reset
        # on every server restart.
        if isinstance(_event, ConversationStateUpdateEvent):
            return
        self.service.stored.updated_at = utc_now()
        update_last_execution_time()


@dataclass
class AutoTitleSubscriber(Subscriber):
    service: EventService

    async def __call__(self, event: Event) -> None:
        # Only act on incoming user messages
        if not isinstance(event, MessageEvent) or event.source != "user":
            return
        # Guard: skip if a title was already set (e.g. by a concurrent task)
        if self.service.stored.title is not None:
            return

        # Extract the message text now, before spawning the background task,
        # to avoid a race where the event hasn't been persisted to the events
        # list yet when title generation tries to read it.
        message_text = extract_message_text(event)
        if not message_text:
            return

        # Precedence: title_llm_profile (if configured and loads) → agent.llm →
        # truncation. This keeps auto-titling non-breaking for consumers who
        # don't configure title_llm_profile.
        title_llm = self._load_title_llm()
        if title_llm is None:
            conversation = self.service._conversation
            title_llm = conversation.agent.llm if conversation else None

        async def _generate_and_save() -> None:
            try:
                loop = asyncio.get_running_loop()
                title = await loop.run_in_executor(
                    None,
                    generate_title_from_message,
                    message_text,
                    title_llm,
                    50,
                )
                if title and self.service.stored.title is None:
                    self.service.stored.title = title
                    self.service.stored.updated_at = utc_now()
                    await self.service.save_meta()
            except Exception:
                logger.warning(
                    f"Auto-title generation failed for "
                    f"conversation {self.service.stored.id}",
                    exc_info=True,
                )

        asyncio.create_task(_generate_and_save())

    def _load_title_llm(self) -> LLM | None:
        """Load the LLM for title generation from profile store.

        Returns:
            LLM instance if title_llm_profile is configured and loads
            successfully, None otherwise. When None is returned, the caller
            falls back to the agent's LLM (and then to message truncation).
        """
        profile_name = self.service.stored.title_llm_profile
        if not profile_name:
            return None

        try:
            from openhands.agent_server.persistence.store import (
                get_llm_profile_store,
            )

            profile_store = get_llm_profile_store()
            return profile_store.load(profile_name, cipher=self.service.cipher)
        except (FileNotFoundError, ValueError) as e:
            logger.warning(
                f"Failed to load title LLM profile '{profile_name}': {e}. "
                "Falling back to the agent's LLM."
            )
            return None


@dataclass
class WebhookSubscriber(Subscriber):
    conversation_id: UUID
    service: EventService
    spec: WebhookSpec
    session_api_key: str | None = None
    queue: list[Event] = field(default_factory=list)
    _flush_timer: asyncio.Task | None = field(default=None, init=False)
    # Per-instance sleep seam so tests override delays without patching the
    # global asyncio.sleep. default_factory (not default) keeps it an instance
    # attribute, else the function would be descriptor-bound as a method.
    _sleep: Callable[[float], Awaitable[None]] = field(
        default_factory=lambda: asyncio.sleep, init=False
    )

    async def __call__(self, event: Event):
        """Add event to queue and post to webhook when buffer size is reached."""
        self.queue.append(event)

        if len(self.queue) >= self.spec.event_buffer_size:
            # Cancel timer since we're flushing due to buffer size
            self._cancel_flush_timer()
            await self._post_events()
        elif not self._flush_timer:
            self._flush_timer = asyncio.create_task(self._flush_after_delay())

    async def close(self):
        """Post any remaining items in the queue to the webhook."""
        # Cancel any pending flush timer
        self._cancel_flush_timer()

        if self.queue:
            await self._post_events()

    async def _post_events(self):
        """Post queued events to the webhook with retry logic."""
        if not self.queue:
            return

        events_to_post = self.queue.copy()
        self.queue.clear()

        # Prepare headers
        headers = self.spec.headers.copy()
        if self.session_api_key:
            headers["X-Session-API-Key"] = self.session_api_key

        # Convert events to a JSON-serializable format. mode="json" is required
        # so types like set and SecretStr become JSON-safe primitives; without
        # it httpx's encoder raises "Object of type set/SecretStr is not JSON
        # serializable", every retry fails identically, and the events are
        # dropped. (Mirrors ConversationWebhookSubscriber.post_conversation_info.)
        event_data = [
            event.model_dump(mode="json")
            if hasattr(event, "model_dump")
            else event.__dict__
            for event in events_to_post
        ]

        # Construct events URL
        events_url = (
            f"{self.spec.base_url.rstrip('/')}/events/{self.conversation_id.hex}"
        )

        # Retry logic
        for attempt in range(self.spec.num_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method="POST",
                        url=events_url,
                        json=event_data,
                        headers=headers,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    logger.debug(
                        f"Successfully posted {len(event_data)} events "
                        f"to webhook {events_url}"
                    )
                    return
            except Exception as e:
                logger.warning(f"Webhook post attempt {attempt + 1} failed: {e}")
                if attempt < self.spec.num_retries:
                    await self._sleep(self.spec.retry_delay)
                else:
                    logger.error(
                        f"Failed to post events to webhook {events_url} "
                        f"after {self.spec.num_retries + 1} attempts"
                    )
                    self.queue.extend(events_to_post)
                    overflow = len(self.queue) - self.spec.max_queue_size
                    if overflow > 0:
                        del self.queue[:overflow]
                        logger.warning(
                            f"Webhook queue exceeded max_queue_size="
                            f"{self.spec.max_queue_size}; dropped {overflow} "
                            f"oldest event(s) for {events_url}."
                        )

    def _cancel_flush_timer(self):
        """Cancel the current flush timer if it exists."""
        if self._flush_timer and not self._flush_timer.done():
            self._flush_timer.cancel()
        self._flush_timer = None

    async def _flush_after_delay(self):
        """Wait for flush_delay seconds then flush events if any exist."""
        try:
            await self._sleep(self.spec.flush_delay)
            # Only flush if there are events in the queue
            if self.queue:
                await self._post_events()
        except asyncio.CancelledError:
            # Timer was cancelled, which is expected behavior
            pass
        finally:
            self._flush_timer = None


@dataclass
class ConversationWebhookSubscriber:
    """Webhook subscriber for conversation lifecycle events (start, pause, stop)."""

    spec: WebhookSpec
    session_api_key: str | None = None
    # Per-instance sleep seam; see WebhookSubscriber._sleep.
    _sleep: Callable[[float], Awaitable[None]] = field(
        default_factory=lambda: asyncio.sleep, init=False
    )

    async def post_conversation_info(self, conversation_info: BaseModel):
        """Post conversation info to the webhook immediately (no batching)."""
        # Prepare headers
        headers = self.spec.headers.copy()
        if self.session_api_key:
            headers["X-Session-API-Key"] = self.session_api_key

        # Construct conversations URL
        conversations_url = f"{self.spec.base_url.rstrip('/')}/conversations"

        # Convert conversation info to serializable format
        conversation_data = conversation_info.model_dump(mode="json")

        # Retry logic
        response = None
        for attempt in range(self.spec.num_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method="POST",
                        url=conversations_url,
                        json=conversation_data,
                        headers=headers,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    logger.debug(
                        f"Successfully posted conversation info "
                        f"to webhook {conversations_url}"
                    )
                    return
            except Exception as e:
                logger.warning(
                    f"Conversation webhook post attempt {attempt + 1} failed: {e}"
                )
                if attempt < self.spec.num_retries:
                    await self._sleep(self.spec.retry_delay)
                else:
                    # Log response content for debugging failures
                    response_content = (
                        response.text if response is not None else "No response"
                    )
                    logger.error(
                        f"Failed to post conversation info to webhook "
                        f"{conversations_url} after {self.spec.num_retries + 1} "
                        f"attempts. Response: {response_content}"
                    )


_conversation_service: ConversationService | None = None


def get_default_conversation_service() -> ConversationService:
    global _conversation_service
    if _conversation_service:
        return _conversation_service

    from openhands.agent_server.config import (
        get_default_config,
    )

    config = get_default_config()
    _conversation_service = ConversationService.get_instance(config)
    return _conversation_service
