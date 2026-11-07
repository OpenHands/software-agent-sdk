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

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
from openhands.agent_server.utils import utc_now
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import PROJECT_TAG_KEY
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.datetime import OpenHandsUUID


logger = get_logger(__name__)

projects_router = APIRouter(prefix="/projects", tags=["Projects"])

PROJECT_TAG = PROJECT_TAG_KEY


class ProjectAttention(str, Enum):
    """What a thread needs from the person, derived from its execution status."""

    NEEDS_YOU = "needs_you"
    WORKING = "working"
    FINISHED = "finished"
    IDLE = "idle"


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
            "(needs_you, working, finished, idle) and newest-updated first "
            "within each group."
        ),
    )


class DeleteResponse(BaseModel):
    deleted: bool = True


# Helpers


def _store(request: Request) -> FileProjectsStore:
    return get_projects_store(get_config(request))


def _load(store: FileProjectsStore) -> PersistedProjects:
    try:
        persisted = store.load()
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


def _update(store: FileProjectsStore, apply) -> PersistedProjects:
    try:
        return store.update(apply)
    except RuntimeError as e:
        logger.error(f"Projects update blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Projects file is corrupted",
        )
    except OSError:
        logger.error("Projects update failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to save projects")


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


async def _join(
    service: ConversationService, project_id: UUID, conversation_id: UUID
) -> None:
    tags = await service.set_conversation_tag(
        conversation_id, PROJECT_TAG, project_id.hex
    )
    if tags is None:
        raise HTTPException(status_code=404, detail="Conversation not found")


def _thread(
    stored: StoredConversation, execution_status: ConversationExecutionStatus
) -> ProjectThread:
    return ProjectThread(
        conversation_id=stored.id,
        title=stored.title,
        execution_status=execution_status,
        attention=_ATTENTION_BY_STATUS.get(execution_status, ProjectAttention.IDLE),
        parent_conversation_id=stored.parent_conversation_id,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


# Routes


@projects_router.get("", response_model=ProjectsListResponse)
async def list_projects(request: Request) -> ProjectsListResponse:
    """List all projects, oldest first."""
    return ProjectsListResponse(projects=list(_load(_store(request)).projects))


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
    if payload.coordinator_conversation_id is not None:
        _check_not_coordinating_elsewhere(
            _load(store), project.id, payload.coordinator_conversation_id
        )
        await _join(service, project.id, payload.coordinator_conversation_id)

    def apply(current: PersistedProjects) -> PersistedProjects:
        return current.model_copy(update={"projects": [*current.projects, project]})

    _update(store, apply)
    return project


@projects_router.get(
    "/{project_id}",
    response_model=ProjectItem,
    responses={404: {"description": "Project not found"}},
)
async def get_project(project_id: UUID, request: Request) -> ProjectItem:
    """Get one project."""
    return _find(_load(_store(request)), project_id)


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
    persisted = _load(store)
    _find(persisted, project_id)

    update: dict = {}
    if payload.name is not None:
        update["name"] = payload.name
    if payload.description is not None:
        update["description"] = payload.description
    if "coordinator_conversation_id" in payload.model_fields_set:
        coordinator_id = payload.coordinator_conversation_id
        if coordinator_id is not None:
            _check_not_coordinating_elsewhere(persisted, project_id, coordinator_id)
            await _join(service, project_id, coordinator_id)
        update["coordinator_conversation_id"] = coordinator_id

    updated: ProjectItem | None = None

    def apply(current: PersistedProjects) -> PersistedProjects:
        nonlocal updated
        projects = []
        for project in current.projects:
            if project.id == project_id:
                project = project.model_copy(update={**update, "updated_at": utc_now()})
                updated = project
            projects.append(project)
        if updated is None:
            return current
        return current.model_copy(update={"projects": projects})

    _update(store, apply)
    if updated is None:
        # Deleted between the read above and the locked write.
        raise HTTPException(status_code=404, detail="Project not found")
    return updated


@projects_router.delete(
    "/{project_id}",
    response_model=DeleteResponse,
    responses={404: {"description": "Project not found"}},
)
async def delete_project(project_id: UUID, request: Request) -> DeleteResponse:
    """Delete a project.

    Conversations are left as they are, including their ``project`` tag; the
    project's threads endpoint answers 404 afterwards.
    """
    removed = False

    def apply(current: PersistedProjects) -> PersistedProjects:
        nonlocal removed
        remaining = [p for p in current.projects if p.id != project_id]
        if len(remaining) == len(current.projects):
            return current
        removed = True
        return current.model_copy(update={"projects": remaining})

    _update(_store(request), apply)
    if not removed:
        raise HTTPException(status_code=404, detail="Project not found")
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
    project = _find(_load(_store(request)), project_id)
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
    persisted = _load(_store(request))
    _find(persisted, project_id)
    _check_not_coordinating_elsewhere(persisted, project_id, conversation_id)
    await _join(service, project_id, conversation_id)
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
    project = _find(_load(_store(request)), project_id)
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
