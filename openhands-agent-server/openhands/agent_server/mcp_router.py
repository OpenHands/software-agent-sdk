"""MCP server management router for OpenHands Agent Server.

Provides CRUD endpoints for registering server-level MCP servers
that can be referenced by ID when creating conversations.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from openhands.agent_server.mcp_service import MCPService
from openhands.agent_server.models import (
    CreateMCPServerRequest,
    MCPServerInfo,
    Success,
    UpdateMCPServerRequest,
)


mcp_router = APIRouter(prefix="/mcp", tags=["MCP Servers"])


def _get_mcp_service(request: Request) -> MCPService:
    service = getattr(request.app.state, "mcp_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP service is not available",
        )
    return service


@mcp_router.post("", status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    request: CreateMCPServerRequest,
    mcp_service: MCPService = Depends(_get_mcp_service),
) -> MCPServerInfo:
    """Register a new server-level MCP server.

    The server configuration is validated and tools are discovered.
    If the server is reachable, its status will be 'active'; otherwise 'error'.
    The configuration is persisted and the server will be automatically
    loaded on subsequent server startups.
    """
    try:
        return mcp_service.create_server(request.id, request.config)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e


@mcp_router.get("")
async def list_mcp_servers(
    mcp_service: MCPService = Depends(_get_mcp_service),
) -> list[MCPServerInfo]:
    """List all registered MCP servers."""
    return mcp_service.list_servers()


@mcp_router.get("/{mcp_id}", responses={404: {"description": "MCP server not found"}})
async def get_mcp_server(
    mcp_id: str,
    mcp_service: MCPService = Depends(_get_mcp_service),
) -> MCPServerInfo:
    """Get details of a registered MCP server."""
    info = mcp_service.get_server(mcp_id)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return info


@mcp_router.patch(
    "/{mcp_id}",
    responses={
        404: {"description": "MCP server not found"},
        409: {"description": "Invalid configuration"},
    },
)
async def update_mcp_server(
    mcp_id: str,
    request: UpdateMCPServerRequest,
    mcp_service: MCPService = Depends(_get_mcp_service),
) -> MCPServerInfo:
    """Update an existing MCP server's configuration.

    The new configuration is validated and tools are re-discovered.
    """
    try:
        return mcp_service.update_server(mcp_id, request.config)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@mcp_router.delete(
    "/{mcp_id}", responses={404: {"description": "MCP server not found"}}
)
async def delete_mcp_server(
    mcp_id: str,
    mcp_service: MCPService = Depends(_get_mcp_service),
) -> Success:
    """Delete a registered MCP server.

    The server configuration is removed from disk and will no longer
    be loaded on server startup.
    """
    deleted = mcp_service.delete_server(mcp_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Success()
