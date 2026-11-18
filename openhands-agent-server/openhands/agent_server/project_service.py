"""Projects' part of the conversation service.

:class:`ProjectService` sits beside
:class:`~openhands.agent_server.conversation_service.ConversationService` and
holds what only Projects needs: starting a coordinator and its workers, the
server-owned tags (membership and board state) and their per-conversation
lock, resolving, the board that watches each member's events, and catching up
on coordinator updates after a restart. It reaches conversations only through
the conversation service's public methods.
"""

import asyncio
import functools
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from openhands.agent_server.event_service import EventService, RunSlot, ServiceInactive
from openhands.agent_server.experimental_features import (
    ASK_USER,
    feature_enabled_now,
)
from openhands.agent_server.launch import LaunchFailure
from openhands.agent_server.models import (
    ConversationInfo,
    StartChildConversationRequest,
    StartChildConversationResponse,
    StartConversationRequest,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.persistence import PersistedSettings, ServerStores
from openhands.agent_server.persistence.models import ProjectItem
from openhands.agent_server.project_board import (
    AGENT_RESOLVE_NOTE,
    ConversationOutcomeSubscriber,
)
from openhands.agent_server.project_memory import MEMORY_INDEX, ProjectMemoryStore
from openhands.agent_server.project_profiles import ensure_project_profile
from openhands.agent_server.project_prompts import (
    SenderRole,
    project_context,
    sender_header,
)
from openhands.agent_server.project_wake import (
    conversation_context,
    latest_turn_start,
    notify_coordinator,
    notify_coordinator_of_stop,
    pass_on_answer,
    reconcile_coordinator_updates,
    relay_user_message,
)
from openhands.agent_server.resolution_judge import make_resolution_judge
from openhands.sdk import LLM, TextContent
from openhands.sdk.context.prompts.presets import PromptPreset
from openhands.sdk.conversation.message_request import SendMessageRequest
from openhands.sdk.conversation.request import AgentLaunchAdditions, ConversationConfig
from openhands.sdk.conversation.types import (
    BOARD_TAG_KEYS,
    PROJECT_TAG_KEY,
    RESOLVE_NOTE_TAG_KEY,
    RESOLVED_TAG_KEY,
    conversation_sender,
)
from openhands.sdk.errors import Conflict
from openhands.sdk.event.ask_user import ASK_USER_TOOL_NAME
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace


if TYPE_CHECKING:
    from openhands.agent_server.conversation_service import ConversationService


logger = get_logger(__name__)


PROJECT_MEMBER_TOOLS = (
    "write_artifact",
    "read_artifact",
    "list_artifacts",
    "read_project_memory",
    "update_project_memory",
)
"""Tools every project conversation gets: save deliverables and keep the
project's memory."""


PROJECT_WORKER_TOOLS = (*PROJECT_MEMBER_TOOLS, "resolve_conversation")
"""Tools the server adds to a conversation a project member starts. Workers
also resolve their own conversation when nothing is left in it; the
coordinator is not on the board, so it does not."""


PROJECT_COORDINATOR_TOOLS = (
    "list_project_conversations",
    "message_project_conversation",
    "read_project_conversation",
    "start_child_conversation",
    *PROJECT_MEMBER_TOOLS,
)
"""Tools the server adds to a coordinator it starts with a new project."""


RESERVED_TAG_KEYS = BOARD_TAG_KEYS | {PROJECT_TAG_KEY}
"""Tags the server owns: project membership and the board's state.

A client's tag ``PATCH`` keeps their current values (see :func:`_client_tags`);
the projects routes change them.
"""


def _client_tags(current: Mapping[str, str], requested: Mapping[str, str]) -> dict:
    """The tags a client's full-set ``PATCH`` leaves on a conversation.

    The reserved keys keep their current values, so a client that read the
    tags a moment ago cannot undo a board update made since, nor set board
    state or membership. Resolving goes through the project's resolve and
    reopen routes.
    """
    tags = {k: v for k, v in requested.items() if k not in RESERVED_TAG_KEYS}
    for key in RESERVED_TAG_KEYS:
        if key in current:
            tags[key] = current[key]
    return tags


async def _logged_turn_sender(event_service: EventService) -> str | None:
    """``sender`` of the latest message that started a turn, from the log.

    For a board that did not see the turn begin (the server restarted in
    between, or the conversation joined mid-turn); ``None`` means the user,
    or no turn found.
    """
    start = await latest_turn_start(event_service)
    return start.sender if start is not None else None


def _agent_llm(event_service: EventService) -> LLM | None:
    try:
        conversation = event_service.get_conversation()
    except ServiceInactive:
        return None
    return getattr(conversation.agent, "llm", None)


def _coordinator_profile_id(stores: ServerStores) -> UUID | None:
    """The predefined coordinator profile, or the user's active one."""
    return ensure_project_profile("coordinator", stores) or _active_agent_profile_id(
        stores
    )


def _active_agent_profile_id(stores: ServerStores) -> UUID | None:
    settings = stores.settings.load() or PersistedSettings()
    active = settings.active_agent_profile_id
    return UUID(str(active)) if active else None


def _load_project(stores: ServerStores, project_hex: str) -> ProjectItem | None:
    persisted = stores.projects.load()
    return next(
        (
            p
            for p in (persisted.projects if persisted else [])
            if p.id.hex == project_hex
        ),
        None,
    )


async def _project_prompt(
    stores: ServerStores | None,
    project_hex: str,
    started_by: tuple[str | None, UUID] | None,
) -> tuple[str | None, SenderRole]:
    """The ``<PROJECT>`` block for a conversation of ``project_hex``.

    ``started_by`` is the title and id of the conversation that starts a
    worker, or ``None`` for the coordinator. Returns the block's body (``None``
    when the project cannot be read) and how the starter relates to the
    project, for the header of the worker's brief.
    """
    try:
        project = (
            await asyncio.to_thread(_load_project, stores, project_hex)
            if stores is not None
            else None
        )
    except (OSError, ValueError):
        logger.warning("Projects unreadable; no project context for %s", project_hex)
        project = None
    role: SenderRole = "member"
    if started_by is not None and project is not None:
        if project.coordinator_conversation_id == started_by[1]:
            role = "coordinator"
    if project is None or stores is None:
        return None, role
    memory = None
    try:
        store = ProjectMemoryStore(stores.projects.persistence_dir)
        index = await asyncio.to_thread(store.get, project.id, MEMORY_INDEX)
        memory = index.content if index is not None else None
    except (ValueError, OSError):
        logger.warning("Project memory unreadable for project %s", project_hex)
    context = project_context(
        project.name,
        project.description,
        started_by=None if started_by is None else (*started_by, role),
        memory=memory,
    )
    return context, role


@dataclass
class ProjectService:
    """Projects on top of a :class:`ConversationService`."""

    conversations: "ConversationService"
    # Per conversation: serializes the read-merge-write of its tags.
    _tag_locks: WeakValueDictionary[UUID, asyncio.Lock] = field(
        default_factory=WeakValueDictionary, init=False
    )
    # The live conversations whose events the board watches.
    _watched: WeakValueDictionary[UUID, EventService] = field(
        default_factory=WeakValueDictionary, init=False
    )
    # Agent resolves still writing; kept so they are not garbage collected.
    _agent_resolves: set[asyncio.Task] = field(default_factory=set, init=False)
    _reconcile_task: asyncio.Task | None = field(default=None, init=False)

    @property
    def stores(self) -> ServerStores | None:
        return self.conversations.stores

    async def enabled(self) -> bool:
        """Whether the user has Projects on right now."""
        return await feature_enabled_now(
            "projects", self.stores.settings if self.stores else None
        )

    # Lifecycle

    def catch_up(self) -> None:
        """Deliver, in the background, coordinator updates a restart left
        undelivered."""
        self._reconcile_task = asyncio.create_task(self._reconcile())

    async def _reconcile(self) -> None:
        try:
            delivered = await reconcile_coordinator_updates(self.conversations)
        except Exception:
            logger.warning("Catching up on coordinator updates failed", exc_info=True)
            return
        if delivered:
            logger.info("Delivered %d missed coordinator update(s)", delivered)

    async def close(self) -> None:
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None

    # The board

    async def watch(self, event_service: EventService) -> None:
        """Let the project board follow a member's events.

        Called when a member's conversation goes live, and when a live
        conversation joins a project. A conversation that never joins one
        carries no Projects machinery. Watching the same live conversation
        twice changes nothing.
        """
        conversation_id = event_service.stored.id
        if self._watched.get(conversation_id) is event_service:
            return
        self._watched[conversation_id] = event_service
        service = self.conversations
        board = ConversationOutcomeSubscriber(
            conversation_id=conversation_id,
            tags=lambda: event_service.stored.tags,
            set_tag=self.set_conversation_tag,
            on_finished=functools.partial(notify_coordinator, service, conversation_id),
            on_user_message=functools.partial(
                relay_user_message, service, conversation_id
            ),
            on_stopped=functools.partial(
                notify_coordinator_of_stop, service, conversation_id
            ),
            on_user_answer=functools.partial(pass_on_answer, service, conversation_id),
            turn_sender_lookup=functools.partial(_logged_turn_sender, event_service),
            projects_enabled=self.enabled,
            judge=make_resolution_judge(
                functools.partial(_agent_llm, event_service),
                functools.partial(conversation_context, event_service),
            ),
        )
        try:
            await event_service.subscribe_internal(board)
        except BaseException:
            self._watched.pop(conversation_id, None)
            raise

    # Tags

    def _tag_lock(self, conversation_id: UUID) -> asyncio.Lock:
        lock = self._tag_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._tag_locks[conversation_id] = lock
        return lock

    async def set_conversation_tag(
        self,
        conversation_id: UUID,
        key: str,
        value: str | None,
        *,
        expected: str | None = None,
        only_if_absent: bool = False,
    ) -> dict[str, str] | None:
        """Set or remove one tag, keeping the conversation's other tags.

        ``UpdateConversationRequest.tags`` replaces the whole set, so callers
        that change a single tag would otherwise race each other. Changes made
        here are serialized; a concurrent ``PATCH`` of the full set still
        replaces it. Setting ``project`` starts the board on the conversation.

        Args:
            conversation_id: The conversation to change.
            key: The tag key.
            value: The new value, or ``None`` to remove the tag.
            expected: When given, change the tag only if it currently holds
                this value.
            only_if_absent: Change nothing when the tag is already set.

        Returns:
            The conversation's tags after the change, or ``None`` when the
            conversation does not exist.

        Raises:
            ValueError: If ``expected`` is given and does not match.
        """
        async with self._tag_lock(conversation_id):
            event_service = await self.conversations.get_event_service(conversation_id)
            if event_service is None:
                return None
            tags = dict(event_service.stored.tags)
            if expected is not None and tags.get(key) != expected:
                raise ValueError(f"Tag '{key}' does not hold the expected value")
            if only_if_absent and key in tags:
                return tags
            if value is None:
                if key not in tags:
                    return tags
                del tags[key]
            else:
                if tags.get(key) == value:
                    return tags
                tags[key] = value
            await self.conversations.update_conversation(
                conversation_id, UpdateConversationRequest(tags=tags)
            )
            if key == PROJECT_TAG_KEY and value is not None:
                await self.watch(event_service)
            return dict(event_service.stored.tags)

    async def update_conversation_from_client(
        self, conversation_id: UUID, request: UpdateConversationRequest
    ) -> bool:
        """``update_conversation`` for a client's ``PATCH``.

        Its tags are merged under the conversation's tag lock, so the
        server-owned keys (:data:`RESERVED_TAG_KEYS`) keep their values; see
        :func:`_client_tags`.
        """
        if request.tags is None:
            return await self.conversations.update_conversation(
                conversation_id, request
            )
        async with self._tag_lock(conversation_id):
            event_service = await self.conversations.get_event_service(conversation_id)
            if event_service is None:
                return False
            tags = _client_tags(event_service.stored.tags, request.tags)
            return await self.conversations.update_conversation(
                conversation_id, request.model_copy(update={"tags": tags})
            )

    async def set_resolved(
        self,
        conversation_id: UUID,
        resolved: bool,
        *,
        by: Literal["user", "agent"] = "user",
    ) -> dict[str, str] | None:
        """Resolve a conversation, or reopen it.

        The user's resolve (``by="user"``) sets ``resolved=user``; the
        conversation's own agent (``by="agent"``) sets ``resolved=auto`` with
        the note :data:`AGENT_RESOLVE_NOTE`, unless the user already resolved
        it. Otherwise the auto-resolve note goes. Returns the tags after the
        change, or ``None`` when the conversation does not exist.
        """
        async with self._tag_lock(conversation_id):
            event_service = await self.conversations.get_event_service(conversation_id)
            if event_service is None:
                return None
            current = dict(event_service.stored.tags)
            if resolved and by == "agent" and current.get(RESOLVED_TAG_KEY) == "user":
                return current
            tags = dict(current)
            tags.pop(RESOLVE_NOTE_TAG_KEY, None)
            tags.pop(RESOLVED_TAG_KEY, None)
            if resolved and by == "agent":
                tags[RESOLVED_TAG_KEY] = "auto"
                tags[RESOLVE_NOTE_TAG_KEY] = AGENT_RESOLVE_NOTE
            elif resolved:
                tags[RESOLVED_TAG_KEY] = "user"
            if tags != current:
                await self.conversations.update_conversation(
                    conversation_id, UpdateConversationRequest(tags=tags)
                )
            return dict(event_service.stored.tags)

    def resolve_for_agent(self, conversation_id: UUID) -> None:
        """Resolve a conversation for its own agent, without waiting.

        The agent asks from inside a tool call, and a running conversation
        holds its state lock for the whole call, so waiting for the state's
        copy of the tags would hold the call until it times out. The tags
        are saved first, so the board sees the resolve at once; the state's
        copy follows when the tool call ends.
        """

        async def resolve() -> None:
            try:
                await self.set_resolved(conversation_id, True, by="agent")
            except Exception:
                logger.warning(
                    "Agent resolve failed for conversation %s",
                    conversation_id,
                    exc_info=True,
                )

        task = asyncio.create_task(resolve())
        self._agent_resolves.add(task)
        task.add_done_callback(self._agent_resolves.discard)

    # Starting conversations

    async def start_coordinator(
        self,
        project_hex: str,
        working_dir: str,
        *,
        agent_profile_id: UUID | None = None,
        title: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> ConversationInfo:
        """Start an idle coordinator conversation for a new project.

        It runs ``agent_profile_id``, or else the predefined coordinator
        profile (or the user's active one when that cannot be made), with
        the coordinator's prompt, project context and project tools added by
        the server, and joins the project through its ``project`` tag. It gets
        no first message: it waits for the user. ``options`` are more
        ``StartConversationRequest`` fields from the client (its own tags,
        a reserved ``conversation_id``, launch additions, policies).

        Raises ValueError when there is no profile to run, the reserved id is
        taken, or it cannot start.
        """
        self.conversations.ensure_active()
        stores = self.stores
        if agent_profile_id is None and stores is None:
            raise ValueError("This server has no profile stores")
        profile_id = agent_profile_id or await asyncio.to_thread(
            _coordinator_profile_id, cast(ServerStores, stores)
        )
        if profile_id is None:
            raise ValueError(
                "No agent profile to start the coordinator from: pass "
                "agent_profile_id or set an active agent profile"
            )
        extra = dict(options or {})
        tags = {**extra.pop("tags", {}), PROJECT_TAG_KEY: project_hex}
        context, _ = await _project_prompt(stores, project_hex, None)
        additions = AgentLaunchAdditions.model_validate(
            extra.pop("agent_launch_additions", None) or {}
        ).model_copy(
            update={
                "prompt_preset": PromptPreset.PROJECT_COORDINATOR,
                "project_context": context,
            }
        )
        start = StartConversationRequest(
            **extra,
            agent_launch_additions=additions,
            workspace=LocalWorkspace(working_dir=working_dir),
            agent_profile_id=profile_id,
            tags=tags,
            autotitle=title is None,
        )
        server_tools = [Tool(name=name) for name in PROJECT_COORDINATOR_TOOLS]
        if await feature_enabled_now(ASK_USER.key, stores.settings if stores else None):
            # Its ask_user also passes on the members' questions, so the user
            # answers them all in the coordinator's conversation. It takes the
            # place of the plain ask_user the feature gives every conversation.
            server_tools.append(
                Tool(name=ASK_USER_TOOL_NAME, params={"for_conversations": True})
            )
        try:
            return await self.conversations.create_conversation(
                start,
                title=title,
                server_tools=server_tools,
                # A concurrent create with the same reserved id must not
                # hand one coordinator to two projects.
                reuse_existing=False,
            )
        except Exception as exc:
            if not isinstance(exc, LaunchFailure):
                raise
            raise ValueError(
                f"Cannot start the coordinator from agent profile {profile_id}: {exc}"
            ) from exc

    async def start_worker(
        self,
        coordinator_service: EventService,
        request: StartChildConversationRequest,
        run_slot: RunSlot,
    ) -> StartChildConversationResponse:
        """A project's new member: a worker from an agent profile, or, with
        no profile to start from, a copy of the coordinator's agent that still runs
        the worker's prompt, project context and tools.

        Only the project's coordinator starts conversations in a project, so
        each member's work is the coordinator's to follow.

        Raises:
            Conflict: If the starting conversation is not the project's coordinator.
        """
        coordinator = coordinator_service.stored
        await self._require_coordinator(coordinator)
        worker_profile = await self._worker_profile(coordinator)
        if worker_profile is not None:
            return await self._start_worker_from_profile(
                coordinator_service, request, worker_profile, run_slot
            )
        project_hex = coordinator.tags[PROJECT_TAG_KEY]
        context, role = await _project_prompt(
            self.stores, project_hex, (coordinator.title, coordinator.id)
        )
        return await self.conversations.start_copied_child(
            coordinator_service,
            request,
            run_slot,
            additions=AgentLaunchAdditions(
                prompt_preset=PromptPreset.PROJECT_WORKER,
                project_context=context,
            ),
            header=sender_header(coordinator.title, coordinator.id, role=role),
            withheld_tools=(),
            extra_tags={PROJECT_TAG_KEY: project_hex},
        )

    async def _require_coordinator(self, coordinator: StoredConversation) -> None:
        stores = self.stores
        project_hex = coordinator.tags[PROJECT_TAG_KEY]
        project = (
            await asyncio.to_thread(_load_project, stores, project_hex)
            if stores is not None
            else None
        )
        if project is None or project.coordinator_conversation_id != coordinator.id:
            raise Conflict(
                "Only the project's coordinator starts conversations in a project"
            )

    async def _worker_profile(self, coordinator: StoredConversation) -> UUID | None:
        """The agent profile a new worker starts from.

        A worker is a normal conversation of the user's, not a copy of the
        coordinator: it runs the project's
        ``worker_agent_profile_id``, or else the user's active agent
        profile. ``None`` (no project, no profile, or a workspace a normal
        start cannot take) keeps the copy of the coordinator's agent.
        """
        project_hex = coordinator.tags.get(PROJECT_TAG_KEY)
        stores = self.stores
        if (
            stores is None
            or not project_hex
            or not isinstance(coordinator.workspace, LocalWorkspace)
        ):
            return None

        def lookup() -> UUID | None:
            try:
                project_id = UUID(hex=project_hex)
            except ValueError:
                return None
            persisted = stores.projects.load()
            project = next(
                (
                    p
                    for p in (persisted.projects if persisted else [])
                    if p.id == project_id
                ),
                None,
            )
            if project is not None and project.worker_agent_profile_id is not None:
                return project.worker_agent_profile_id
            predefined = ensure_project_profile("worker", stores)
            if predefined is not None:
                return predefined
            return _active_agent_profile_id(stores)

        try:
            return await asyncio.to_thread(lookup)
        except Exception:
            logger.warning(
                "Cannot choose a worker agent profile for project %s; the new "
                "conversation copies its coordinator's agent",
                project_hex,
                exc_info=True,
            )
            return None

    async def _start_worker_from_profile(
        self,
        coordinator_service: EventService,
        request: StartChildConversationRequest,
        profile_id: UUID,
        run_slot: RunSlot,
    ) -> StartChildConversationResponse:
        """Start a worker from the project's worker agent profile.

        It keeps what makes it part of the project: the ``project`` tag, the
        coordinator's workspace (in a worktree when asked), the parent link to
        the coordinator, the user's confirmation policy, plugins, hooks and
        secrets, the coordinator as sender of the task (with a header saying
        so), the worker's prompt and project context, and the project tools a
        worker uses. It does not take the coordinator's agent, launch
        additions or client tools.
        """
        coordinator = coordinator_service.stored
        coordinator_id = coordinator.id
        worker_id = uuid4()
        context, role = await _project_prompt(
            self.stores,
            coordinator.tags[PROJECT_TAG_KEY],
            (coordinator.title, coordinator_id),
        )
        config = {
            name: getattr(coordinator, name) for name in ConversationConfig.model_fields
        }
        config.update(
            conversation_id=worker_id,
            parent_conversation_id=coordinator_id,
            worktree=request.isolation == "worktree",
            worktree_options=None,
            secrets_encrypted=False,
            client_tools=[],
            agent_launch_additions=AgentLaunchAdditions(
                prompt_preset=PromptPreset.PROJECT_WORKER,
                project_context=context,
            ),
            initial_message=SendMessageRequest(
                role="user",
                content=[
                    TextContent(
                        text=sender_header(coordinator.title, coordinator_id, role=role)
                    ),
                    TextContent(text=request.task),
                ],
            ),
            # A worker joins the project and nothing else: the coordinator's
            # other tags (an app's role or controller markers) describe it.
            tags={PROJECT_TAG_KEY: coordinator.tags[PROJECT_TAG_KEY]},
            autotitle=request.title is None,
            prompt=None,
        )
        start = StartConversationRequest(
            **config,
            agent_profile_id=profile_id,
            max_budget_per_run=(
                coordinator_service.get_conversation().max_budget_per_run
            ),
        )
        try:
            info = await self.conversations.create_conversation(
                start,
                run_slot=run_slot,
                title=request.title,
                initial_sender=conversation_sender(coordinator_id),
                server_tools=[Tool(name=name) for name in PROJECT_WORKER_TOOLS],
            )
        except Exception as exc:
            if not isinstance(exc, LaunchFailure):
                raise
            raise ValueError(
                f"Cannot start the conversation from agent profile {profile_id}: {exc}"
            ) from exc
        workspace = cast(LocalWorkspace, info.workspace)
        coordinator_workspace = cast(LocalWorkspace, coordinator.workspace)
        shared = await asyncio.to_thread(
            lambda: (
                Path(workspace.working_dir).resolve()
                == Path(coordinator_workspace.working_dir).resolve()
            )
        )
        return StartChildConversationResponse(
            conversation_id=worker_id,
            parent_conversation_id=coordinator_id,
            status=info.execution_status.value,
            title=info.title,
            workspace=str(workspace.working_dir),
            isolation="shared" if shared else "worktree",
        )
