"""Static webserver for a conversation's workspace.

Exposes the contents of a conversation's workspace directory at
``/conversations/{conversation_id}/workspace/{file_path:path}``.  When the
``api_router`` mounts this router under the ``/api`` prefix, the public URL
becomes ``/api/conversations/{conversation_id}/workspace/...``.

Behaves like a plain static file server:
- A request for a file returns that file with an inferred ``Content-Type``.
- A request that resolves to a directory serves ``index.html`` if present,
  otherwise returns 404.
- Path traversal outside of the workspace is rejected.
"""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import LocalWorkspace


logger = get_logger(__name__)

workspace_router = APIRouter(prefix="/conversations", tags=["Workspace"])


def conversation_workspace_url_path(conversation_id: UUID | str) -> str:
    """Return the relative URL prefix that serves a conversation's workspace.

    The returned path always ends with a trailing slash so callers can
    join it directly with relative file paths.
    """
    return f"/api/conversations/{conversation_id}/workspace/"


async def _resolve_workspace_dir(
    conversation_id: UUID,
    conversation_service: ConversationService,
) -> Path:
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    workspace = event_service.stored.workspace
    if not isinstance(workspace, LocalWorkspace):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation workspace is not local; cannot be served",
        )
    workspace_dir = Path(workspace.working_dir).resolve()
    if not workspace_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace directory does not exist",
        )
    return workspace_dir


def _resolve_target(workspace_dir: Path, file_path: str) -> Path:
    """Resolve ``file_path`` under ``workspace_dir`` safely.

    Rejects any path that escapes ``workspace_dir`` after resolution.
    """
    candidate = (workspace_dir / file_path).resolve()
    if candidate != workspace_dir and not candidate.is_relative_to(workspace_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is outside the workspace",
        )
    return candidate


def _serve_path(workspace_dir: Path, file_path: str) -> FileResponse:
    target = _resolve_target(workspace_dir, file_path)

    if target.is_dir():
        index_file = target / "index.html"
        if not index_file.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No index.html in directory",
            )
        return FileResponse(path=index_file)

    if not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return FileResponse(path=target)


@workspace_router.get(
    "/{conversation_id}/workspace",
    responses={404: {"description": "File or conversation not found"}},
)
async def serve_workspace_root(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> FileResponse:
    """Serve ``index.html`` from the conversation's workspace root."""
    workspace_dir = await _resolve_workspace_dir(conversation_id, conversation_service)
    return _serve_path(workspace_dir, "")


@workspace_router.get(
    "/{conversation_id}/workspace/{file_path:path}",
    responses={404: {"description": "File or conversation not found"}},
)
async def serve_workspace_file(
    conversation_id: UUID,
    file_path: str,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> FileResponse:
    """Serve a file (or directory ``index.html``) from the workspace."""
    workspace_dir = await _resolve_workspace_dir(conversation_id, conversation_service)
    return _serve_path(workspace_dir, file_path)
