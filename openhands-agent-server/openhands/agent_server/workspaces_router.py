"""HTTP endpoints for managing the user's saved workspaces.

Workspaces are local directories the GUI surfaces in its workspace picker.
They are persisted on the agent-server (file-backed JSON) rather than in
each browser's localStorage so that every client connected to the same
agent-server sees the same list.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import (
    PersistedWorkspaces,
    WorkspaceItem,
    WorkspaceParentItem,
    get_workspaces_store,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

workspaces_router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceItem] = Field(default_factory=list)
    workspace_parents: list[WorkspaceParentItem] = Field(
        default_factory=list, alias="workspaceParents"
    )
    model_config = ConfigDict(populate_by_name=True)


class AddWorkspacesRequest(BaseModel):
    workspaces: list[WorkspaceItem]


class AddWorkspaceParentsRequest(BaseModel):
    parents: list[WorkspaceParentItem]


class DeleteResponse(BaseModel):
    deleted: bool = True


def _to_response(persisted: PersistedWorkspaces) -> WorkspacesListResponse:
    return WorkspacesListResponse.model_validate(
        {
            "workspaces": list(persisted.workspaces),
            "workspace_parents": list(persisted.workspace_parents),
        }
    )


@workspaces_router.get(
    "",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def list_workspaces(request: Request) -> WorkspacesListResponse:
    """Return the saved workspaces and workspace parents."""
    config = get_config(request)
    store = get_workspaces_store(config)
    try:
        persisted = store.load()
    except (OSError, PermissionError):
        logger.error("Workspaces list failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to read workspaces")
    if persisted is None:
        # ``load()`` collapses "file missing" and "file present but
        # unreadable/corrupted/future-schema" into the same ``None``. Returning
        # an empty list for the second case would hide the user's data and let
        # a subsequent POST overwrite the still-on-disk file with defaults.
        if store._path.exists():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workspaces file is corrupted",
            )
        persisted = PersistedWorkspaces()
    return _to_response(persisted)


@workspaces_router.post(
    "",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def add_workspaces(
    request: Request, payload: AddWorkspacesRequest
) -> WorkspacesListResponse:
    """Append workspaces to the saved list. Idempotent — dedupes by ``path``."""
    config = get_config(request)
    store = get_workspaces_store(config)

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        # Dedupe against the existing list AND within the incoming payload,
        # otherwise a body like ``[{"path": "/a"}, {"path": "/a"}]`` would
        # persist both entries despite the endpoint contract.
        seen_paths = {w.path for w in current.workspaces}
        new_items: list[WorkspaceItem] = []
        for w in payload.workspaces:
            if w.path in seen_paths:
                continue
            seen_paths.add(w.path)
            new_items.append(w)
        if not new_items:
            return current
        return current.model_copy(
            update={"workspaces": [*current.workspaces, *new_items]}
        )

    try:
        updated = store.update(apply)
    except RuntimeError as e:
        logger.error(f"Workspaces add blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspaces file is corrupted",
        )
    except (OSError, PermissionError):
        logger.error("Workspaces add failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to save workspaces")

    return _to_response(updated)


@workspaces_router.delete("", response_model=DeleteResponse)
async def delete_workspace(
    request: Request,
    path: Annotated[str, Query(description="Absolute workspace path to remove")],
) -> DeleteResponse:
    """Remove a workspace by its absolute path."""
    config = get_config(request)
    store = get_workspaces_store(config)

    removed = False

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        nonlocal removed
        remaining = [w for w in current.workspaces if w.path != path]
        if len(remaining) == len(current.workspaces):
            return current
        removed = True
        return current.model_copy(update={"workspaces": remaining})

    try:
        store.update(apply)
    except RuntimeError as e:
        logger.error(f"Workspaces delete blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspaces file is corrupted",
        )
    except (OSError, PermissionError):
        logger.error("Workspaces delete failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to update workspaces")

    if not removed:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return DeleteResponse()


@workspaces_router.post(
    "/parents",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def add_workspace_parents(
    request: Request, payload: AddWorkspaceParentsRequest
) -> WorkspacesListResponse:
    """Append workspace parents. Idempotent — dedupes by ``path``."""
    config = get_config(request)
    store = get_workspaces_store(config)

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        # Dedupe against the existing list AND within the incoming payload —
        # see ``add_workspaces`` above for the same rationale.
        seen_paths = {p.path for p in current.workspace_parents}
        new_items: list[WorkspaceParentItem] = []
        for p in payload.parents:
            if p.path in seen_paths:
                continue
            seen_paths.add(p.path)
            new_items.append(p)
        if not new_items:
            return current
        return current.model_copy(
            update={
                "workspace_parents": [*current.workspace_parents, *new_items],
            }
        )

    try:
        updated = store.update(apply)
    except RuntimeError as e:
        logger.error(f"Workspace parents add blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspaces file is corrupted",
        )
    except (OSError, PermissionError):
        logger.error("Workspace parents add failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to save workspaces")

    return _to_response(updated)


@workspaces_router.delete("/parents", response_model=DeleteResponse)
async def delete_workspace_parent(
    request: Request,
    path: Annotated[str, Query(description="Absolute workspace parent path to remove")],
) -> DeleteResponse:
    """Remove a workspace parent by its absolute path."""
    config = get_config(request)
    store = get_workspaces_store(config)

    removed = False

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        nonlocal removed
        remaining = [p for p in current.workspace_parents if p.path != path]
        if len(remaining) == len(current.workspace_parents):
            return current
        removed = True
        return current.model_copy(update={"workspace_parents": remaining})

    try:
        store.update(apply)
    except RuntimeError as e:
        logger.error(f"Workspace parents delete blocked: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspaces file is corrupted",
        )
    except (OSError, PermissionError):
        logger.error("Workspace parents delete failed - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to update workspaces")

    if not removed:
        raise HTTPException(status_code=404, detail="Workspace parent not found")
    return DeleteResponse()
