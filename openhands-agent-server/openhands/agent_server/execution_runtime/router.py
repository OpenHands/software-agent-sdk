from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from openhands.sdk.tool import Observation, ToolExecutor


class ToolExecutionRequest(BaseModel):
    tool_name: str
    action: dict[str, Any]


class ToolExecutionResponse(BaseModel):
    observation: dict[str, Any]


execution_runtime_router = APIRouter(prefix="/execution", tags=["Execution runtime"])


def _get_executor(request: Request, tool_name: str) -> ToolExecutor:
    executors: dict[str, ToolExecutor] = request.app.state.execution_tool_executors
    executor = executors.get(tool_name)
    if executor is not None:
        return executor

    config = request.app.state.config
    working_dir = str(config.execution_working_dir)
    if tool_name == "terminal":
        from openhands.tools.terminal.impl import TerminalExecutor

        executor = TerminalExecutor(working_dir=working_dir)
    elif tool_name == "file_editor":
        from openhands.tools.file_editor.impl import FileEditorExecutor

        executor = FileEditorExecutor(workspace_root=working_dir)
    elif tool_name == "grep":
        from openhands.tools.grep.impl import GrepExecutor

        executor = GrepExecutor(working_dir=working_dir)
    elif tool_name == "glob":
        from openhands.tools.glob.impl import GlobExecutor

        executor = GlobExecutor(working_dir=working_dir)
    elif tool_name == "apply_patch":
        from openhands.tools.apply_patch.definition import ApplyPatchExecutor

        executor = ApplyPatchExecutor(workspace_root=working_dir)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tool is not supported by the execution runtime: {tool_name}",
        )
    executors[tool_name] = executor
    return executor


@execution_runtime_router.post("/tools", response_model=ToolExecutionResponse)
def execute_tool(
    payload: ToolExecutionRequest,
    request: Request,
) -> ToolExecutionResponse:
    if payload.tool_name == "terminal":
        from openhands.tools.terminal.definition import TerminalAction

        action = TerminalAction.model_validate(payload.action)
    elif payload.tool_name == "file_editor":
        from openhands.tools.file_editor.definition import FileEditorAction

        action = FileEditorAction.model_validate(payload.action)
    elif payload.tool_name == "grep":
        from openhands.tools.grep.definition import GrepAction

        action = GrepAction.model_validate(payload.action)
    elif payload.tool_name == "glob":
        from openhands.tools.glob.definition import GlobAction

        action = GlobAction.model_validate(payload.action)
    elif payload.tool_name == "apply_patch":
        from openhands.tools.apply_patch.definition import ApplyPatchAction

        action = ApplyPatchAction.model_validate(payload.action)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Tool is not supported by the execution runtime: {payload.tool_name}"
            ),
        )

    observation: Observation = _get_executor(request, payload.tool_name)(action)
    return ToolExecutionResponse(
        observation=observation.model_dump(mode="json", exclude_none=True)
    )
