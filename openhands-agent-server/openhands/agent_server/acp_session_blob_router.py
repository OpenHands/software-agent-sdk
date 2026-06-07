"""Export/import of an ACP CLI's session-transcript subtree, per conversation.

The transport half of native ACP resume across sandbox recycles (#1126): an
app server pulls the allowlisted session blob at turn boundaries
(``GET``), stores it durably, and seeds it back into a *fresh* sandbox before
re-issuing the conversation start (``PUT``) so the CLI's ``session/load``
finds its transcripts again. Both directions are restricted to the provider's
``session_subtrees`` allowlist — credentials (``auth.json``,
``.credentials.json``) and global CLI state never ride along; auth is
re-established per launch by file-secret materialisation / env secrets.

The routes are deliberately conversation-independent (pure filesystem, keyed
on ``<conversations_path>/<conversation_id.hex>/acp/<provider>``): export
still works after the conversation errored or closed, and import runs before
the conversation exists on a fresh sandbox.
"""

import asyncio
import tarfile
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path as PathParam, Request, Response
from pydantic import BaseModel

from openhands.agent_server.config import get_default_config
from openhands.sdk.logger import get_logger
from openhands.sdk.settings.acp_providers import ACPProviderInfo, get_acp_provider
from openhands.sdk.settings.acp_session_blob import (
    export_acp_session_blob,
    import_acp_session_blob,
)


logger = get_logger(__name__)
acp_session_blob_router = APIRouter(
    prefix="/acp_session_blob", tags=["ACP Session Blobs"]
)

# Session transcripts are KB–low-MB JSONL files; anything near this cap is a
# malformed or hostile payload, not a real snapshot.
MAX_BLOB_SIZE = 256 * 1024 * 1024


class ImportSessionBlobResponse(BaseModel):
    files_written: int


def _resolve_provider(provider: str) -> ACPProviderInfo:
    info = get_acp_provider(provider)
    if info is None:
        raise HTTPException(
            status_code=404, detail=f"unknown ACP provider: {provider!r}"
        )
    if not info.session_subtrees:
        raise HTTPException(
            status_code=422,
            detail=f"ACP provider {provider!r} does not support session snapshots",
        )
    return info


def _data_root(conversation_id: UUID, provider: ACPProviderInfo):
    config = get_default_config()
    return config.conversations_path / conversation_id.hex / "acp" / provider.key


@acp_session_blob_router.get(
    "/{conversation_id}/{provider}",
    responses={
        200: {"content": {"application/gzip": {}}},
        204: {"description": "No session files exist for this conversation"},
    },
)
async def export_session_blob(
    conversation_id: UUID,
    provider: Annotated[str, PathParam(title="ACP provider key, e.g. 'codex'")],
) -> Response:
    """Download the allowlisted session-transcript blob (tar.gz)."""
    info = _resolve_provider(provider)
    blob = await asyncio.to_thread(
        export_acp_session_blob, _data_root(conversation_id, info), info
    )
    if blob is None:
        return Response(status_code=204)
    return Response(content=blob, media_type="application/gzip")


@acp_session_blob_router.put("/{conversation_id}/{provider}")
async def import_session_blob(
    conversation_id: UUID,
    provider: Annotated[str, PathParam(title="ACP provider key, e.g. 'codex'")],
    request: Request,
) -> ImportSessionBlobResponse:
    """Seed-if-absent restore of a session blob into the conversation dir."""
    info = _resolve_provider(provider)
    blob = await request.body()
    if not blob:
        raise HTTPException(status_code=400, detail="empty session blob")
    if len(blob) > MAX_BLOB_SIZE:
        raise HTTPException(status_code=413, detail="session blob too large")
    try:
        files_written = await asyncio.to_thread(
            import_acp_session_blob, _data_root(conversation_id, info), info, blob
        )
    except (ValueError, OSError, tarfile.TarError) as exc:
        logger.warning(
            "Rejected ACP session blob for %s/%s: %s",
            conversation_id.hex,
            provider,
            exc,
        )
        raise HTTPException(
            status_code=422, detail=f"invalid session blob: {exc}"
        ) from exc
    return ImportSessionBlobResponse(files_written=files_written)
