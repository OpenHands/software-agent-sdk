"""Conversation-addressed APIs with workspace and terminal-history context.

Local processes and VSCode services still share the host; these path
checks are routing safeguards, not a sandbox for arbitrary shell commands.
"""

from collections.abc import Callable
from copy import deepcopy
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
from openhands.agent_server.file_router import file_router
from openhands.agent_server.git_router import git_router
from openhands.agent_server.vscode_router import (
    VSCodeUrlResponse,
    get_vscode_url,
    vscode_router,
)


async def bind_local_conversation_runtime(
    runtime_conversation_id: UUID, request: Request
) -> None:
    conversation_service = get_conversation_service(request)
    event_service = await get_event_service(
        runtime_conversation_id, request, conversation_service
    )
    if event_service is None:
        raise HTTPException(404, "Conversation not found")
    request.state.runtime_event_service = event_service
    workspace_root = Path(
        event_service.get_conversation().workspace.working_dir
    ).resolve()
    for name in ("path", "workspace_dir"):
        requested_path = request.path_params.get(name) or request.query_params.get(name)
        if requested_path is not None and (
            not Path(requested_path).is_absolute()
            or not Path(requested_path).resolve().is_relative_to(workspace_root)
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
        if kwargs.get("response_class") is FileResponse:
            responses = deepcopy(kwargs.get("responses", {}))
            responses.get(200, {}).get("content", {}).pop("application/json", None)
            kwargs["responses"] = responses
        super().add_api_route(path, endpoint, **kwargs)


def create_runtime_router(route_class: type[APIRoute] = APIRoute) -> APIRouter:
    router = RuntimeRouter(
        prefix="/conversations/{runtime_conversation_id}",
        route_class=route_class,
        dependencies=[Depends(bind_local_conversation_runtime)],
    )
    for source in (
        bash_router,
        file_router,
        git_router,
    ):
        router.include_router(source)
    router.add_api_route("/vscode/url", get_runtime_vscode_url, methods=["GET"])
    for route in vscode_router.routes:
        if isinstance(route, APIRoute) and route.path != "/vscode/url":
            router.add_api_route(
                route.path, route.endpoint, methods=list(route.methods)
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
