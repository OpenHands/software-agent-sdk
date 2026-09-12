"""Conversation-addressed APIs with workspace and terminal-history context.

Local processes and desktop/VSCode services still share the host; these path
checks are routing safeguards, not a sandbox for arbitrary shell commands.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

from openhands.agent_server.bash_router import bash_router
from openhands.agent_server.dependencies import (
    get_conversation_service,
    get_event_service,
)
from openhands.agent_server.desktop_router import desktop_router
from openhands.agent_server.file_router import file_router
from openhands.agent_server.git_router import git_router
from openhands.agent_server.mcp_router import MCPTestResponse, test_mcp_server
from openhands.agent_server.vscode_router import (
    VSCodeUrlResponse,
    get_vscode_url,
    vscode_router,
)


async def require_local_runtime(
    runtime_conversation_id: UUID, request: Request
) -> None:
    service = get_conversation_service(request)
    event_service = await get_event_service(runtime_conversation_id, service)
    request.state.runtime_event_service = event_service
    root = Path(event_service.get_conversation().workspace.working_dir).resolve()
    for name in ("path", "workspace_dir"):
        path = request.path_params.get(name) or request.query_params.get(name)
        if path is not None and (
            not Path(path).is_absolute()
            or not Path(path).resolve().is_relative_to(root)
        ):
            raise HTTPException(
                422, f"{name} must be inside the conversation workspace"
            )
    trajectory_id = request.path_params.get("conversation_id")
    if trajectory_id is not None:
        try:
            matches = UUID(trajectory_id) == runtime_conversation_id
        except ValueError as exc:
            raise HTTPException(422, "Invalid trajectory conversation id") from exc
        if not matches:
            raise HTTPException(
                422, "Trajectory must belong to the selected conversation"
            )


class RuntimeRouter(APIRouter):
    def add_api_route(
        self, path: str, endpoint: Callable[..., Any], **kwargs: Any
    ) -> None:
        kwargs["route_class_override"] = self.route_class
        if path in {
            "/file/download",
            "/file/archive",
            "/file/download-trajectory/{conversation_id}",
        }:
            kwargs["response_class"] = FileResponse
            kwargs["responses"] = {
                **(kwargs.get("responses") or {}),
                200: {
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    }
                },
            }
        super().add_api_route(path, endpoint, **kwargs)


def create_runtime_router(route_class: type[APIRoute] = APIRoute) -> APIRouter:
    router = RuntimeRouter(
        prefix="/conversations/{runtime_conversation_id}",
        route_class=route_class,
        dependencies=[Depends(require_local_runtime)],
    )
    for source in (
        bash_router,
        file_router,
        git_router,
        desktop_router,
    ):
        for route in source.routes:
            if isinstance(route, APIRoute):
                router.add_api_route(
                    route.path,
                    route.endpoint,
                    methods=list(route.methods),
                    response_model=route.response_model,
                    status_code=route.status_code,
                    tags=route.tags,
                    dependencies=route.dependencies,
                    summary=route.summary,
                    description=route.description,
                    response_description=route.response_description,
                    responses=route.responses,
                    deprecated=route.deprecated,
                    response_model_include=route.response_model_include,
                    response_model_exclude=route.response_model_exclude,
                    response_model_by_alias=route.response_model_by_alias,
                    response_model_exclude_unset=route.response_model_exclude_unset,
                    response_model_exclude_defaults=route.response_model_exclude_defaults,
                    response_model_exclude_none=route.response_model_exclude_none,
                    response_class=route.response_class,
                    name=route.name,
                    callbacks=route.callbacks,
                    openapi_extra=route.openapi_extra,
                )
    router.add_api_route("/vscode/url", get_runtime_vscode_url, methods=["GET"])
    for route in vscode_router.routes:
        if isinstance(route, APIRoute) and route.path != "/vscode/url":
            router.add_api_route(
                route.path, route.endpoint, methods=list(route.methods)
            )
    router.add_api_route(
        "/mcp/test",
        test_mcp_server,
        methods=["POST"],
        response_model=MCPTestResponse,
        response_model_exclude_none=True,
    )
    return router


async def get_runtime_vscode_url(
    request: Request,
    base_url: str | None = None,
    workspace_dir: str | None = None,
) -> VSCodeUrlResponse:
    event_service = request.state.runtime_event_service
    return await get_vscode_url(
        base_url,
        workspace_dir or event_service.get_conversation().workspace.working_dir,
    )


def add_legacy_runtime_routes(
    router: APIRouter,
    prefix: str,
    endpoint: Callable[..., Any],
    bare_endpoint: Callable[..., Any],
) -> None:
    """Register the deprecated query-scoped runtime compatibility contract."""
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    router.add_api_route(
        f"/{prefix}/{{tail:path}}",
        endpoint,
        methods=methods,
        deprecated=True,
        description=(
            "Deprecated since v1.48.0 and scheduled for removal in v1.53.0. "
            "Use /api/conversations/{id}/{service}/... instead of ?cid={id} "
            "routing. This deprecation applies only to Docker compatibility routes."
        ),
    )
    router.add_api_route(
        f"/{prefix}",
        bare_endpoint,
        methods=methods,
        deprecated=True,
        description=(
            "Deprecated since v1.48.0 and scheduled for removal in v1.53.0. "
            "Use /api/conversations/{id}/{service}/... instead of ?cid={id} "
            "routing. This deprecation applies only to Docker compatibility routes."
        ),
    )
