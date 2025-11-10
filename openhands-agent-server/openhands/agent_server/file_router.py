from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Path as FastApiPath,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from openhands.agent_server.bash_service import get_default_bash_event_service
from openhands.agent_server.config import get_default_config
from openhands.agent_server.conversation_service import get_default_conversation_service
from openhands.agent_server.models import ExecuteBashRequest, Success
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)
file_router = APIRouter(prefix="/file", tags=["Files"])
config = get_default_config()
conversation_service = get_default_conversation_service()
bash_event_service = get_default_bash_event_service()


@file_router.post("/upload/{path:path}")
async def upload_file(
    path: Annotated[str, FastApiPath(alias="path", description="Absolute file path.")],
    file: Annotated[UploadFile, File(...)],
) -> Success:
    """Upload a file to the workspace."""
    logger.info(f"Uploading file: {path}")
    try:
        target_path = Path(path)
        if not target_path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must be absolute",
            )

        # Ensure target directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Stream the file to disk to avoid memory issues with large files
        with open(target_path, "wb") as f:
            while chunk := await file.read(8192):  # Read in 8KB chunks
                f.write(chunk)

        logger.info(f"Uploaded file to {target_path}")
        return Success()

    except Exception as e:
        logger.error(f"Failed to upload file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}",
        )


@file_router.get("/download/{path:path}")
async def download_file(
    path: Annotated[str, FastApiPath(description="Absolute file path.")],
) -> FileResponse:
    """Download a file from the workspace."""
    logger.info(f"Downloading file: {path}")
    try:
        target_path = Path(path)
        if not target_path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must be absolute",
            )

        if not target_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
            )

        if not target_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a file"
            )

        return FileResponse(
            path=target_path,
            filename=target_path.name,
            media_type="application/octet-stream",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download file: {str(e)}",
        )


@file_router.get("/download-trajectory/{conversation_id}")
async def download_trajectory(
    conversation_id: UUID,
) -> FileResponse:
    """Download a file from the workspace."""
    config = get_default_config()
    temp_file = config.conversations_path / f"{conversation_id.hex}.zip"
    conversation_dir = config.conversations_path / conversation_id.hex
    _, task = await bash_event_service.start_bash_command(
        ExecuteBashRequest(command=f"zip -r {temp_file} {conversation_dir}")
    )
    await task
    return FileResponse(
        path=temp_file,
        filename=temp_file.name,
        media_type="application/octet-stream",
        background=BackgroundTask(temp_file.unlink),
    )
