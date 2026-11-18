"""HTTP endpoints for managing the user's saved workspaces.

Workspaces are local directories the GUI surfaces in its workspace picker.
They are persisted on the agent-server (file-backed JSON) rather than in
each browser's localStorage so that every client connected to the same
agent-server sees the same list.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from openhands.agent_server.dependencies import get_workspaces_store
from openhands.agent_server.persistence import (
    FileWorkspacesStore,
    PersistedWorkspaces,
    WorkspaceItem,
    WorkspaceParentItem,
    run_store,
)
from openhands.sdk.errors import Conflict, NotFound, OperationFailed
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


class RenameWorkspaceRequest(BaseModel):
    # The 64-character cap the GUI applies is a product rule; the registry
    # itself accepts anything the schema allows, including untrimmed input.
    name: str = Field(..., min_length=1, max_length=256)


class DeleteResponse(BaseModel):
    deleted: bool = True


def _to_response(persisted: PersistedWorkspaces) -> WorkspacesListResponse:
    return WorkspacesListResponse.model_validate(
        {
            "workspaces": list(persisted.workspaces),
            "workspace_parents": list(persisted.workspace_parents),
        }
    )


def _rename_entries[EntryT: (WorkspaceItem, WorkspaceParentItem)](
    entries: list[EntryT], path: str, name: str
) -> tuple[list[EntryT], bool, bool]:
    """Relabel every entry at ``path``.

    Returns ``(entries, matched, changed)``: ``path`` is the identity, so only
    ``name`` moves. An entry already carrying ``name`` is left as the same
    object, which lets the caller return the document unchanged for a no-op
    rename and skip the write.
    """
    matched = False
    changed = False
    renamed: list[EntryT] = []
    for entry in entries:
        if entry.path != path:
            renamed.append(entry)
            continue
        matched = True
        if entry.name != name:
            entry = entry.model_copy(update={"name": name})
            changed = True
        renamed.append(entry)
    return renamed, matched, changed


@workspaces_router.get(
    "",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def list_workspaces(
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> WorkspacesListResponse:
    """Return the saved workspaces and workspace parents."""

    def read() -> tuple[PersistedWorkspaces | None, bool]:
        persisted = store.load()
        # ``load()`` collapses "file missing" and "file present but
        # unreadable/corrupted/future-schema" into the same ``None``. Returning
        # an empty list for the second case would hide the user's data and let
        # a subsequent POST overwrite the still-on-disk file with defaults.
        return persisted, persisted is None and store._path.exists()

    try:
        persisted, corrupted = await run_store(read)
    except (OSError, PermissionError):
        logger.error("Workspaces list failed - file I/O error")
        raise OperationFailed("Failed to read workspaces")
    if persisted is None:
        if corrupted:
            raise Conflict("Workspaces file is corrupted")
        persisted = PersistedWorkspaces()
    return _to_response(persisted)


@workspaces_router.post(
    "",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def add_workspaces(
    payload: AddWorkspacesRequest,
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> WorkspacesListResponse:
    """Append workspaces to the saved list. Idempotent — dedupes by ``path``."""

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
        updated = await run_store(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Workspaces add blocked: {e}")
        raise Conflict("Workspaces file is corrupted")
    except (OSError, PermissionError):
        logger.error("Workspaces add failed - file I/O error")
        raise OperationFailed("Failed to save workspaces")

    return _to_response(updated)


@workspaces_router.delete("", response_model=DeleteResponse)
async def delete_workspace(
    path: Annotated[str, Query(description="Absolute workspace path to remove")],
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> DeleteResponse:
    """Remove a workspace by its absolute path."""

    removed = False

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        nonlocal removed
        remaining = [w for w in current.workspaces if w.path != path]
        if len(remaining) == len(current.workspaces):
            return current
        removed = True
        return current.model_copy(update={"workspaces": remaining})

    try:
        await run_store(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Workspaces delete blocked: {e}")
        raise Conflict("Workspaces file is corrupted")
    except (OSError, PermissionError):
        logger.error("Workspaces delete failed - file I/O error")
        raise OperationFailed("Failed to update workspaces")

    if not removed:
        raise NotFound("Workspace not found")
    return DeleteResponse()


@workspaces_router.patch(
    "",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def rename_workspace(
    payload: RenameWorkspaceRequest,
    path: Annotated[str, Query(description="Absolute workspace path to rename")],
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> WorkspacesListResponse:
    """Rename a saved workspace. ``path`` is the identity; only the label changes."""

    matched = False

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        nonlocal matched
        renamed, matched, changed = _rename_entries(
            current.workspaces, path, payload.name
        )
        if not changed:
            return current
        return current.model_copy(update={"workspaces": renamed})

    try:
        updated = await run_store(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Workspaces rename blocked: {e}")
        raise Conflict("Workspaces file is corrupted")
    except (OSError, PermissionError):
        logger.error("Workspaces rename failed - file I/O error")
        raise OperationFailed("Failed to update workspaces")

    if not matched:
        raise NotFound("Workspace not found")
    return _to_response(updated)


@workspaces_router.post(
    "/parents",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def add_workspace_parents(
    payload: AddWorkspaceParentsRequest,
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> WorkspacesListResponse:
    """Append workspace parents. Idempotent — dedupes by ``path``."""

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
        updated = await run_store(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Workspace parents add blocked: {e}")
        raise Conflict("Workspaces file is corrupted")
    except (OSError, PermissionError):
        logger.error("Workspace parents add failed - file I/O error")
        raise OperationFailed("Failed to save workspaces")

    return _to_response(updated)


@workspaces_router.delete("/parents", response_model=DeleteResponse)
async def delete_workspace_parent(
    path: Annotated[str, Query(description="Absolute workspace parent path to remove")],
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> DeleteResponse:
    """Remove a workspace parent by its absolute path."""

    removed = False

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        nonlocal removed
        remaining = [p for p in current.workspace_parents if p.path != path]
        if len(remaining) == len(current.workspace_parents):
            return current
        removed = True
        return current.model_copy(update={"workspace_parents": remaining})

    try:
        await run_store(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Workspace parents delete blocked: {e}")
        raise Conflict("Workspaces file is corrupted")
    except (OSError, PermissionError):
        logger.error("Workspace parents delete failed - file I/O error")
        raise OperationFailed("Failed to update workspaces")

    if not removed:
        raise NotFound("Workspace parent not found")
    return DeleteResponse()


@workspaces_router.patch(
    "/parents",
    response_model=WorkspacesListResponse,
    response_model_exclude_none=True,
)
async def rename_workspace_parent(
    payload: RenameWorkspaceRequest,
    path: Annotated[str, Query(description="Absolute workspace parent path to rename")],
    store: FileWorkspacesStore = Depends(get_workspaces_store),
) -> WorkspacesListResponse:
    """Rename a workspace parent. ``path`` is the identity; only the label changes."""

    matched = False

    def apply(current: PersistedWorkspaces) -> PersistedWorkspaces:
        nonlocal matched
        renamed, matched, changed = _rename_entries(
            current.workspace_parents, path, payload.name
        )
        if not changed:
            return current
        return current.model_copy(update={"workspace_parents": renamed})

    try:
        updated = await run_store(store.update, apply)
    except RuntimeError as e:
        logger.error(f"Workspace parents rename blocked: {e}")
        raise Conflict("Workspaces file is corrupted")
    except (OSError, PermissionError):
        logger.error("Workspace parents rename failed - file I/O error")
        raise OperationFailed("Failed to update workspaces")

    if not matched:
        raise NotFound("Workspace parent not found")
    return _to_response(updated)
