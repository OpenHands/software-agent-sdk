"""HTTP endpoints for projects.

A project groups the conversations that work toward one goal. The person
mainly talks to one of them, the coordinator; the others are its threads.
Projects are persisted on the agent-server (file-backed JSON) so that every
client connected to the same server sees the same list.

Membership lives on the conversations, not in the project record: a
conversation belongs to a project when its ``project`` tag holds the project's
id. Child conversations copy their parent's tags, so the threads a coordinator
starts with ``start_child_conversation`` join its project without another call.
"""

import asyncio
import mimetypes
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.persistence import (
    FileProjectsStore,
    PersistedProjects,
    ProjectItem,
    get_projects_store,
)
from openhands.agent_server.project_artifacts import (
    MAX_ARTIFACT_BYTES,
    ArtifactSource,
    ArtifactTooLarge,
    ProjectArtifact,
    ProjectArtifactStore,
    resolve_workspace_file,
    valid_artifact_name,
)
from openhands.agent_server.project_board import ResolvedBy, TurnOutcome
from openhands.agent_server.utils import utc_now
from openhands.sdk import Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import (
    PROJECT_TAG_KEY,
    RESOLVED_TAG_KEY,
    TURN_TAG_KEY,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.datetime import OpenHandsUUID


logger = get_logger(__name__)

projects_router = APIRouter(prefix="/projects", tags=["Projects"])

PROJECT_TAG = PROJECT_TAG_KEY
# Set by OpenHands automations on the conversations they start.
AUTOMATION_ID_TAG_KEY = "automationid"


class ProjectAttention(str, Enum):
    """What a thread needs from the person.

    Derived from the execution status, then refined by the thread's tags: a
    finished thread whose last message asked something needs input, and a
    resolved thread is resolved unless it is blocked on the person.
    """

    NEEDS_YOU = "needs_you"
    NEEDS_INPUT = "needs_input"
    WORKING = "working"
    FINISHED = "finished"
    IDLE = "idle"
    RESOLVED = "resolved"


_ATTENTION_BY_STATUS = {
    ConversationExecutionStatus.WAITING_FOR_CONFIRMATION: ProjectAttention.NEEDS_YOU,
    ConversationExecutionStatus.PAUSED: ProjectAttention.NEEDS_YOU,
    ConversationExecutionStatus.ERROR: ProjectAttention.NEEDS_YOU,
    ConversationExecutionStatus.STUCK: ProjectAttention.NEEDS_YOU,
    ConversationExecutionStatus.RUNNING: ProjectAttention.WORKING,
    ConversationExecutionStatus.FINISHED: ProjectAttention.FINISHED,
    ConversationExecutionStatus.IDLE: ProjectAttention.IDLE,
}
_ATTENTION_ORDER = {
    attention: index for index, attention in enumerate(ProjectAttention)
}


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    coordinator_conversation_id: UUID | None = Field(
        default=None,
        description=(
            "An existing conversation to make the coordinator. It is tagged "
            "as a member of the new project."
        ),
    )


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    coordinator_conversation_id: UUID | None = Field(
        default=None,
        description=(
            "A conversation to make the coordinator; it is tagged as a member. "
            "An explicit null clears the coordinator and leaves the "
            "conversation a member."
        ),
    )


class ProjectsListResponse(BaseModel):
    projects: list[ProjectItem] = Field(default_factory=list)


class ProjectThread(BaseModel):
    """A conversation in a project, with what it needs from the person."""

    conversation_id: OpenHandsUUID
    title: str | None = None
    execution_status: ConversationExecutionStatus
    attention: ProjectAttention
    resolved: ResolvedBy | None = Field(
        default=None,
        description=(
            "`user` when the person resolved the thread, `auto` when the "
            "server did because the critic scored the finished task at least "
            "0.9. Backed by the `resolved` tag; a new user message clears it."
        ),
    )
    turn: TurnOutcome | None = Field(
        default=None,
        description=(
            "How the agent's last run ended: `asked` when its final message "
            "asked the person something, `answered` otherwise. Backed by the "
            "`turn` tag; a new user message clears it."
        ),
    )
    automation_id: str | None = Field(
        default=None,
        description="The automation that started this thread, if any.",
    )
    parent_conversation_id: OpenHandsUUID | None = None
    created_at: datetime
    updated_at: datetime


class ProjectThreadsResponse(BaseModel):
    project_id: OpenHandsUUID
    coordinator: ProjectThread | None = Field(
        default=None,
        description="The coordinator conversation, when it is set and exists.",
    )
    threads: list[ProjectThread] = Field(
        default_factory=list,
        description=(
            "Every other member conversation, grouped by attention "
            "(needs_you, needs_input, working, finished, idle, resolved) and "
            "newest-updated first within each group."
        ),
    )


class DeleteResponse(BaseModel):
    deleted: bool = True


class ProjectThreadMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, description="The message to send.")


ProjectThreadRunOutcome = Literal[
    "started", "queued", "waiting_for_confirmation", "not_run"
]


class ProjectThreadMessageResponse(BaseModel):
    conversation_id: OpenHandsUUID
    previous_status: ConversationExecutionStatus = Field(
        description="The thread's execution status just before the message."
    )
    run: ProjectThreadRunOutcome = Field(
        description=(
            "What happened about running the thread. `started`: a run was "
            "started for the message. `queued`: a run was already active; it "
            "takes the message, or the thread runs again when it ends, unless "
            "that run stops at a confirmation first. "
            "`waiting_for_confirmation`: the thread reached a confirmation "
            "while the message was being delivered, so it was not run and the "
            "message waits for the person's decision. `not_run`: a stop "
            "arrived meanwhile and the thread was left as it is."
        )
    )


# Helpers


def _store(request: Request) -> FileProjectsStore:
    return get_projects_store(get_config(request))


async def _load(store: FileProjectsStore) -> PersistedProjects:
    try:
        persisted = await asyncio.to_thread(store.load)
    except OSError:
        logger.error("Projects read failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to read projects")
    if persisted is None:
        # ``load()`` returns None both for a missing file and for one it cannot
        # parse. Answering an empty list for the second would hide the user's
        # data, so report it instead.
        if store.exists():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Projects file is corrupted",
            )
        persisted = PersistedProjects()
    return persisted


async def _update(
    store: FileProjectsStore,
    apply: Callable[[PersistedProjects], PersistedProjects],
) -> PersistedProjects:
    """Apply ``apply`` under the file lock, off the event loop.

    ``apply`` may raise ``HTTPException`` to refuse the change; nothing is
    written then.
    """
    try:
        return await asyncio.to_thread(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Projects update blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Projects file is corrupted",
        )
    except OSError:
        logger.error("Projects update failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to save projects")


def _without(current: PersistedProjects, project_id: UUID) -> PersistedProjects:
    remaining = [p for p in current.projects if p.id != project_id]
    if len(remaining) == len(current.projects):
        return current
    return current.model_copy(update={"projects": remaining})


def _find_or_none(persisted: PersistedProjects, project_id: UUID) -> ProjectItem | None:
    return next((p for p in persisted.projects if p.id == project_id), None)


def _find(persisted: PersistedProjects, project_id: UUID) -> ProjectItem:
    for project in persisted.projects:
        if project.id == project_id:
            return project
    raise HTTPException(status_code=404, detail="Project not found")


def _check_not_coordinating_elsewhere(
    persisted: PersistedProjects, project_id: UUID, conversation_id: UUID
) -> None:
    for project in persisted.projects:
        if (
            project.id != project_id
            and project.coordinator_conversation_id == conversation_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation is the coordinator of another project",
            )


async def _require_conversation(
    service: ConversationService, conversation_id: UUID
) -> None:
    if await service.get_event_service(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")


async def _join(
    service: ConversationService, project_id: UUID, conversation_id: UUID
) -> bool:
    """Tag the conversation as a member; ``False`` when it does not exist."""
    tags = await service.set_conversation_tag(
        conversation_id, PROJECT_TAG, project_id.hex
    )
    return tags is not None


async def _join_or_undo(
    service: ConversationService,
    store: FileProjectsStore,
    project_id: UUID,
    conversation_id: UUID,
    undo: Callable[[PersistedProjects], PersistedProjects],
) -> None:
    """Tag a coordinator just saved on a project; undo the save if that fails."""
    try:
        joined = await _join(service, project_id, conversation_id)
    except Exception:
        await _update(store, undo)
        raise
    if not joined:
        await _update(store, undo)
        raise HTTPException(status_code=404, detail="Conversation not found")


def _replace_project(
    current: PersistedProjects, project_id: UUID, update: dict
) -> tuple[PersistedProjects, ProjectItem | None]:
    projects = []
    updated: ProjectItem | None = None
    for project in current.projects:
        if project.id == project_id:
            project = project.model_copy(update={**update, "updated_at": utc_now()})
            updated = project
        projects.append(project)
    if updated is None:
        return current, None
    return current.model_copy(update={"projects": projects}), updated


def _tag_value(tags: dict[str, str], key: str, allowed: tuple[str, ...]) -> Any:
    value = tags.get(key)
    return value if value in allowed else None


def _attention(
    execution_status: ConversationExecutionStatus,
    resolved: str | None,
    turn: str | None,
) -> ProjectAttention:
    attention = _ATTENTION_BY_STATUS.get(execution_status, ProjectAttention.IDLE)
    if attention == ProjectAttention.NEEDS_YOU:
        return attention
    if resolved is not None:
        return ProjectAttention.RESOLVED
    if attention == ProjectAttention.FINISHED and turn == "asked":
        return ProjectAttention.NEEDS_INPUT
    return attention


def _thread(
    stored: StoredConversation, execution_status: ConversationExecutionStatus
) -> ProjectThread:
    resolved = _tag_value(stored.tags, RESOLVED_TAG_KEY, ("user", "auto"))
    turn = _tag_value(stored.tags, TURN_TAG_KEY, ("asked", "answered"))
    return ProjectThread(
        conversation_id=stored.id,
        title=stored.title,
        execution_status=execution_status,
        attention=_attention(execution_status, resolved, turn),
        resolved=resolved,
        turn=turn,
        automation_id=stored.tags.get(AUTOMATION_ID_TAG_KEY) or None,
        parent_conversation_id=stored.parent_conversation_id,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


# Routes


@projects_router.get("", response_model=ProjectsListResponse)
async def list_projects(request: Request) -> ProjectsListResponse:
    """List all projects, oldest first."""
    return ProjectsListResponse(projects=list((await _load(_store(request))).projects))


@projects_router.post(
    "",
    response_model=ProjectItem,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Coordinator conversation not found"},
        409: {"description": "Coordinator already leads another project"},
    },
)
async def create_project(
    request: Request,
    payload: CreateProjectRequest,
    service: ConversationService = Depends(get_conversation_service),
) -> ProjectItem:
    """Create a project, optionally with an existing conversation as coordinator."""
    store = _store(request)
    project = ProjectItem(
        id=uuid4(),
        name=payload.name,
        description=payload.description,
        coordinator_conversation_id=payload.coordinator_conversation_id,
    )
    coordinator_id = payload.coordinator_conversation_id
    if coordinator_id is not None:
        await _require_conversation(service, coordinator_id)

    def apply(current: PersistedProjects) -> PersistedProjects:
        if coordinator_id is not None:
            _check_not_coordinating_elsewhere(current, project.id, coordinator_id)
        return current.model_copy(update={"projects": [*current.projects, project]})

    await _update(store, apply)
    # Tag only once the record exists, so a refused write leaves no tag for
    # a project that was never saved.
    if coordinator_id is not None:
        await _join_or_undo(
            service,
            store,
            project.id,
            coordinator_id,
            lambda current: _without(current, project.id),
        )
    return project


@projects_router.get(
    "/{project_id}",
    response_model=ProjectItem,
    responses={404: {"description": "Project not found"}},
)
async def get_project(project_id: UUID, request: Request) -> ProjectItem:
    """Get one project."""
    return _find(await _load(_store(request)), project_id)


@projects_router.patch(
    "/{project_id}",
    response_model=ProjectItem,
    responses={
        404: {"description": "Project or coordinator conversation not found"},
        409: {"description": "Coordinator already leads another project"},
    },
)
async def update_project(
    project_id: UUID,
    request: Request,
    payload: UpdateProjectRequest,
    service: ConversationService = Depends(get_conversation_service),
) -> ProjectItem:
    """Rename a project, change its description, or set or clear its coordinator."""
    store = _store(request)
    previous = _find(await _load(store), project_id)

    update: dict = {}
    if payload.name is not None:
        update["name"] = payload.name
    if payload.description is not None:
        update["description"] = payload.description
    coordinator_id: UUID | None = None
    if "coordinator_conversation_id" in payload.model_fields_set:
        coordinator_id = payload.coordinator_conversation_id
        if coordinator_id is not None:
            await _require_conversation(service, coordinator_id)
        update["coordinator_conversation_id"] = coordinator_id

    updated: ProjectItem | None = None

    def apply(current: PersistedProjects) -> PersistedProjects:
        nonlocal updated
        if coordinator_id is not None:
            _check_not_coordinating_elsewhere(current, project_id, coordinator_id)
        changed, updated = _replace_project(current, project_id, update)
        return changed

    await _update(store, apply)
    if updated is None:
        # Deleted between the read above and the locked write.
        raise HTTPException(status_code=404, detail="Project not found")
    if coordinator_id is not None:
        saved = updated

        def undo(current: PersistedProjects) -> PersistedProjects:
            # Undo only our own change: a later write to this project wins.
            if _find_or_none(current, project_id) != saved:
                return current
            restore = previous.model_dump(
                include={"name", "description", "coordinator_conversation_id"}
            )
            former = previous.coordinator_conversation_id
            if former is not None and any(
                p.coordinator_conversation_id == former
                for p in current.projects
                if p.id != project_id
            ):
                restore["coordinator_conversation_id"] = None
            return _replace_project(current, project_id, restore)[0]

        await _join_or_undo(service, store, project_id, coordinator_id, undo)
    return updated


@projects_router.delete(
    "/{project_id}",
    response_model=DeleteResponse,
    responses={404: {"description": "Project not found"}},
)
async def delete_project(project_id: UUID, request: Request) -> DeleteResponse:
    """Delete a project.

    Conversations are left as they are, including their ``project`` tag; the
    project's threads endpoint answers 404 afterwards. The project's artifacts
    are deleted.
    """
    removed = False

    def apply(current: PersistedProjects) -> PersistedProjects:
        nonlocal removed
        remaining = _without(current, project_id)
        removed = remaining is not current
        return remaining

    await _update(_store(request), apply)
    if not removed:
        raise HTTPException(status_code=404, detail="Project not found")
    await asyncio.to_thread(_artifacts(request).delete_project, project_id)
    return DeleteResponse()


@projects_router.get(
    "/{project_id}/threads",
    response_model=ProjectThreadsResponse,
    responses={404: {"description": "Project not found"}},
)
async def list_project_threads(
    project_id: UUID,
    request: Request,
    service: ConversationService = Depends(get_conversation_service),
) -> ProjectThreadsResponse:
    """List a project's conversations, grouped by what they need from the person.

    ``attention`` is derived from ``execution_status``: waiting for
    confirmation, paused, error and stuck are ``needs_you``; running is
    ``working``; finished is ``finished``; idle is ``idle``.
    """
    project = _find(await _load(_store(request)), project_id)
    members = await service.list_tagged_conversations(PROJECT_TAG, project_id.hex)

    coordinator: ProjectThread | None = None
    threads: list[ProjectThread] = []
    for stored, execution_status in members:
        thread = _thread(stored, execution_status)
        if stored.id == project.coordinator_conversation_id:
            coordinator = thread
        else:
            threads.append(thread)
    # ``members`` is newest-updated first and ``sort`` is stable.
    threads.sort(key=lambda thread: _ATTENTION_ORDER[thread.attention])
    return ProjectThreadsResponse(
        project_id=project.id, coordinator=coordinator, threads=threads
    )


@projects_router.put(
    "/{project_id}/threads/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "Project or conversation not found"},
        409: {"description": "Conversation is the coordinator of another project"},
    },
)
async def add_project_thread(
    project_id: UUID,
    conversation_id: UUID,
    request: Request,
    service: ConversationService = Depends(get_conversation_service),
) -> Response:
    """Add an existing conversation to a project. Idempotent.

    Sets the conversation's ``project`` tag and keeps its other tags. A
    conversation belongs to at most one project, so this moves it from any
    other project, unless it coordinates that one.
    """
    persisted = await _load(_store(request))
    _find(persisted, project_id)
    _check_not_coordinating_elsewhere(persisted, project_id, conversation_id)
    if not await _join(service, project_id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@projects_router.delete(
    "/{project_id}/threads/{conversation_id}",
    response_model=DeleteResponse,
    responses={
        404: {"description": "Project not found, or conversation not a member"},
        409: {"description": "Conversation is the project's coordinator"},
    },
)
async def remove_project_thread(
    project_id: UUID,
    conversation_id: UUID,
    request: Request,
    service: ConversationService = Depends(get_conversation_service),
) -> DeleteResponse:
    """Remove a conversation from a project by clearing its ``project`` tag.

    The conversation itself is not changed otherwise. Child conversations keep
    their own tag. Clear the coordinator before removing it.
    """
    project = _find(await _load(_store(request)), project_id)
    if project.coordinator_conversation_id == conversation_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation is the project's coordinator",
        )
    try:
        tags = await service.set_conversation_tag(
            conversation_id, PROJECT_TAG, None, expected=project_id.hex
        )
    except ValueError:
        tags = None
    if tags is None:
        raise HTTPException(status_code=404, detail="Conversation not in project")
    return DeleteResponse()


@projects_router.post(
    "/{project_id}/threads/{conversation_id}/messages",
    response_model=ProjectThreadMessageResponse,
    responses={
        404: {"description": "Project not found, or conversation not a member"},
        409: {"description": "The thread is waiting for confirmation"},
        429: {"description": "Message saved, but the server run limit is full"},
    },
)
async def message_project_thread(
    project_id: UUID,
    conversation_id: UUID,
    request: Request,
    payload: ProjectThreadMessageRequest,
    service: ConversationService = Depends(get_conversation_service),
) -> ProjectThreadMessageResponse:
    """Send a message to a project member and run it, without approving anything.

    Unlike ``POST /api/conversations/{id}/events`` with ``run``, this never
    runs a conversation that is waiting for confirmation, because that run
    would approve its pending actions. A thread already waiting is refused
    with 409 and nothing is saved. A thread that reaches a confirmation while
    the message is being delivered keeps the message and is not run
    (``run: "waiting_for_confirmation"``); the refusal is decided under the
    conversation's run lock.
    """
    _find(await _load(_store(request)), project_id)
    event_service = await service.get_event_service(conversation_id)
    if (
        event_service is None
        or event_service.stored.tags.get(PROJECT_TAG) != project_id.hex
    ):
        raise HTTPException(status_code=404, detail="Conversation not in project")

    # An unlocked read: it only spares the person an unrelated message in a
    # thread that is already waiting. The guarded run below is the safeguard.
    previous_status = (await event_service.get_state()).execution_status
    if previous_status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation is waiting for confirmation",
        )
    message = Message(role="user", content=[TextContent(text=payload.text)])
    outcome: ProjectThreadRunOutcome
    try:
        outcome = (
            await event_service.send_message(
                message, run=True, refuse_pending_confirmation=True
            )
            or "not_run"
        )
    except ValueError as e:
        if str(e) != "waiting_for_confirmation":
            raise
        outcome = "waiting_for_confirmation"
    return ProjectThreadMessageResponse(
        conversation_id=conversation_id,
        previous_status=previous_status,
        run=outcome,
    )


# Artifacts


class ProjectArtifactsResponse(BaseModel):
    artifacts: list[ProjectArtifact] = Field(
        default_factory=list, description="Newest-updated first."
    )


class SaveConversationArtifactRequest(BaseModel):
    conversation_id: UUID = Field(
        description="A member conversation whose workspace holds the file."
    )
    path: str = Field(
        min_length=1,
        description=(
            "The file, relative to the conversation's working directory or "
            "absolute inside it."
        ),
    )
    name: str | None = Field(
        default=None,
        description="The artifact's name; the file's own name when left out.",
    )


def _artifacts(request: Request) -> ProjectArtifactStore:
    return ProjectArtifactStore(_store(request).persistence_dir)


def _artifact_name(name: str) -> str:
    if not valid_artifact_name(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid artifact name: use a flat file name of letters, "
                "digits, spaces and ._()+-, not starting with a dot"
            ),
        )
    return name


async def _save_artifact(
    store: ProjectArtifactStore,
    project_id: UUID,
    name: str,
    stream: BinaryIO,
    *,
    source: ArtifactSource,
    conversation_id: UUID | None = None,
) -> ProjectArtifact:
    try:
        return await asyncio.to_thread(
            store.save,
            project_id,
            name,
            stream,
            source=source,
            conversation_id=conversation_id,
        )
    except ArtifactTooLarge:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Artifact is larger than {MAX_ARTIFACT_BYTES} bytes",
        )


@projects_router.get(
    "/{project_id}/artifacts",
    response_model=ProjectArtifactsResponse,
    responses={404: {"description": "Project not found"}},
)
async def list_project_artifacts(
    project_id: UUID, request: Request
) -> ProjectArtifactsResponse:
    """List the files the person uploaded to the project or its agents saved."""
    _find(await _load(_store(request)), project_id)
    artifacts = await asyncio.to_thread(_artifacts(request).list, project_id)
    return ProjectArtifactsResponse(artifacts=artifacts)


@projects_router.post(
    "/{project_id}/artifacts",
    response_model=ProjectArtifact,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid artifact name"},
        404: {"description": "Project not found"},
        413: {"description": "File too large"},
    },
)
async def upload_project_artifact(
    project_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> ProjectArtifact:
    """Upload a file to the project. An artifact of the same name is replaced."""
    _find(await _load(_store(request)), project_id)
    artifact_name = _artifact_name(name or Path(file.filename or "").name)
    return await _save_artifact(
        _artifacts(request), project_id, artifact_name, file.file, source="user"
    )


@projects_router.post(
    "/{project_id}/artifacts/from-conversation",
    response_model=ProjectArtifact,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid name, or path outside the workspace"},
        404: {"description": "Project not found, or conversation not a member"},
        413: {"description": "File too large"},
    },
)
async def save_conversation_artifact(
    project_id: UUID,
    payload: SaveConversationArtifactRequest,
    request: Request,
    service: ConversationService = Depends(get_conversation_service),
) -> ProjectArtifact:
    """Copy a file from a member conversation's workspace into the project.

    The path must stay inside the conversation's working directory, symlinks
    included. An artifact of the same name is replaced.
    """
    _find(await _load(_store(request)), project_id)
    event_service = await service.get_event_service(payload.conversation_id)
    if (
        event_service is None
        or event_service.stored.tags.get(PROJECT_TAG) != project_id.hex
    ):
        raise HTTPException(status_code=404, detail="Conversation not in project")
    working_dir = Path(event_service.stored.workspace.working_dir)
    try:
        source = await asyncio.to_thread(
            resolve_workspace_file, working_dir, payload.path
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    artifact_name = _artifact_name(payload.name or source.name)
    with open(source, "rb") as stream:
        return await _save_artifact(
            _artifacts(request),
            project_id,
            artifact_name,
            stream,
            source="agent",
            conversation_id=payload.conversation_id,
        )


@projects_router.get(
    "/{project_id}/artifacts/{name}",
    response_class=FileResponse,
    responses={404: {"description": "Project or artifact not found"}},
)
async def download_project_artifact(
    project_id: UUID, name: str, request: Request
) -> FileResponse:
    """Download an artifact."""
    _find(await _load(_store(request)), project_id)
    found = await asyncio.to_thread(
        _artifacts(request).get, project_id, _artifact_name(name)
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    _, path = found
    return FileResponse(
        path=path,
        filename=name,
        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
    )


@projects_router.delete(
    "/{project_id}/artifacts/{name}",
    response_model=DeleteResponse,
    responses={404: {"description": "Project or artifact not found"}},
)
async def delete_project_artifact(
    project_id: UUID, name: str, request: Request
) -> DeleteResponse:
    """Delete an artifact."""
    _find(await _load(_store(request)), project_id)
    if not await asyncio.to_thread(
        _artifacts(request).delete, project_id, _artifact_name(name)
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return DeleteResponse()
