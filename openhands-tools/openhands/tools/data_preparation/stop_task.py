"""Stop a running DataFlow pipeline task on the Pyromind platform.

When a user previews partial output and decides the run is not what they
want, the agent should stop the platform-side task before adjusting the
pipeline and resubmitting. The task is identified by ``task_id`` directly,
or resolved from a ``run_id`` / ``output_dir`` recorded by
``df_submit_pipeline``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

import httpx
from pydantic import Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.tools.data_preparation.platform_submit import (
    TASK_ASSOCIATION_DIRNAME,
    DataPreparationTaskStore,
)
from openhands.tools.pyromind_dataset.definition import (
    _decode_json_response,
    _normalize_headers,
    _resolve_conversation_headers,
    _resolve_secret_headers,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


PRE_STOP_URL = "https://pre-api-portal.pyromind.ai/std2/studio_api/api/stop_task"
PROD_STOP_URL = "https://api-portal.pyromind.ai/std2/studio_api/api/stop_task"
_PROD_APP_ENVS = {"prod", "production", "online"}

TOOL_DESCRIPTION = """\
Stop a running DataFlow pipeline task on the platform.

Identify the task with `task_id` (from df_submit_pipeline), or pass the
`run_id` / `output_dir` returned by df_submit_pipeline and the tool resolves
the task for you. Call this when the user previews partial output, decides
the run is not what they want, and asks to adjust the pipeline — stop the
running task first, then edit and resubmit.
"""


def _default_stop_url() -> str:
    app_env = os.getenv("APP_ENV", "dev").strip().lower()
    if app_env in _PROD_APP_ENVS:
        return PROD_STOP_URL
    return PRE_STOP_URL


# ---------------------------------------------------------------------------
# Action / Observation
# ---------------------------------------------------------------------------


class DfStopTaskAction(Action):
    """Stop a running DataFlow pipeline task."""

    task_id: str | None = Field(
        default=None,
        description=(
            "Platform task id returned by df_submit_pipeline. Preferred when available."
        ),
    )
    run_id: str | None = Field(
        default=None,
        description="Run id returned by df_submit_pipeline, used to look up the task.",
    )
    output_dir: str | None = Field(
        default=None,
        description=(
            "Storage output directory returned by df_submit_pipeline, used to "
            "look up the task."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Stop pipeline task: ", style="bold red")
        target = self.task_id or self.run_id or self.output_dir or "<unknown>"
        content.append(target)
        return content


class DfStopTaskObservation(Observation):
    """Result of a stop-task request."""

    task_id: str | None = Field(
        default=None, description="The resolved platform task id that was stopped."
    )
    stopped: bool = Field(
        default=False, description="Whether the stop request succeeded."
    )
    status: str = Field(default="Failed", description="Stopped or Failed.")

    @property
    def visualize(self) -> Text:
        text = Text()
        style = "bold green" if self.stopped else "bold red"
        text.append(f"Stop task: {self.status}\n", style=style)
        text.append(self.text)
        return text


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class DfStopTaskExecutor(ToolExecutor[DfStopTaskAction, DfStopTaskObservation]):
    """Resolve the task id and call the platform stop_task API."""

    def __init__(
        self,
        *,
        stop_url: str | None = None,
        headers: dict[str, str] | None = None,
        secret_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        task_store_dir: str | None = None,
    ) -> None:
        self._stop_url = (stop_url or _default_stop_url()).rstrip("/")
        self._headers = dict(headers or {})
        self._secret_headers = dict(secret_headers or {})
        self._timeout = timeout
        self._task_store_dir = (
            Path(task_store_dir) if task_store_dir is not None else None
        )

    def __call__(
        self,
        action: DfStopTaskAction,
        conversation: BaseConversation | None = None,
    ) -> DfStopTaskObservation:
        task_id = self._resolve_task_id(action, conversation)
        if task_id is None:
            return DfStopTaskObservation.from_text(
                text=(
                    "Could not resolve a task to stop. Provide task_id, or the "
                    "run_id / output_dir returned by df_submit_pipeline."
                ),
                task_id=None,
                stopped=False,
                status="Failed",
                is_error=True,
            )

        headers = self._resolved_headers(conversation)
        result = self._post_stop(task_id, headers)
        if isinstance(result, str):
            return DfStopTaskObservation.from_text(
                text=f"Failed to stop task {task_id}: {result}",
                task_id=task_id,
                stopped=False,
                status="Failed",
                is_error=True,
            )

        failure = _failure_message(result)
        if failure is not None:
            return DfStopTaskObservation.from_text(
                text=f"Failed to stop task {task_id}: {failure}",
                task_id=task_id,
                stopped=False,
                status="Failed",
                is_error=True,
            )

        return DfStopTaskObservation.from_text(
            text=f"Task {task_id} stop request accepted.",
            task_id=task_id,
            stopped=True,
            status="Stopped",
        )

    # -- Resolution ---------------------------------------------------------

    def _resolve_task_id(
        self, action: DfStopTaskAction, conversation: BaseConversation | None
    ) -> str | None:
        if action.task_id and action.task_id.strip():
            return action.task_id.strip()

        store = self._task_store(conversation)
        if store is None:
            return None
        if action.run_id and action.run_id.strip():
            association = store.get_by_run_id(action.run_id.strip())
            if association is not None:
                return association.task_id
        if action.output_dir and action.output_dir.strip():
            association = store.get_by_output_dir(action.output_dir.strip())
            if association is not None:
                return association.task_id
        return None

    def _task_store(
        self, conversation: BaseConversation | None
    ) -> DataPreparationTaskStore | None:
        if self._task_store_dir is not None:
            return DataPreparationTaskStore(self._task_store_dir)
        if conversation is None:
            return None
        workspace = cast(Any, conversation).workspace
        conversations_dir = Path(workspace.working_dir).resolve().parent
        return DataPreparationTaskStore(conversations_dir / TASK_ASSOCIATION_DIRNAME)

    # -- HTTP ---------------------------------------------------------------

    def _resolved_headers(
        self, conversation: BaseConversation | None
    ) -> dict[str, str]:
        headers = {"accept": "*/*", "content-type": "application/json", **self._headers}
        headers.update(_resolve_conversation_headers(conversation))
        headers.update(_resolve_secret_headers(conversation, self._secret_headers))
        return headers

    def _post_stop(self, task_id: str, headers: dict[str, str]) -> dict[str, Any] | str:
        try:
            response = httpx.post(
                self._stop_url,
                headers=headers,
                json={"task_id": _coerce_task_id(task_id)},
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            return f"{type(exc).__name__}: {exc}"
        return _decode_json_response(response, "Pyromind stop_task API")


def _coerce_task_id(task_id: str) -> int | str:
    # The studio_api stop_task contract expects a numeric task_id; send an int
    # when the stored id is digit-only, otherwise pass the raw value through.
    return int(task_id) if task_id.isdigit() else task_id


def _failure_message(payload: dict[str, Any]) -> str | None:
    success = payload.get("success")
    if success is False:
        message = payload.get("message")
        return str(message) if message else "stop_task reported failure"
    code = payload.get("code")
    if isinstance(code, int) and code not in (0, 200):
        message = payload.get("message")
        return str(message) if message else f"stop_task returned code {code}"
    return None


# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------


class DfStopTaskTool(ToolDefinition[DfStopTaskAction, DfStopTaskObservation]):
    """Tool definition for stopping a DataFlow pipeline task."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[Self]:
        stop_url_value = params.pop("stop_url", None)
        stop_url = str(stop_url_value) if stop_url_value is not None else None
        headers = _normalize_headers(params.pop("headers", None))
        secret_headers = _normalize_headers(
            params.pop("storage_secret_headers", params.pop("secret_headers", None))
        )
        timeout = float(params.pop("timeout", 30.0))
        task_store_dir_value = params.pop("task_store_dir", None)
        task_store_dir = (
            str(task_store_dir_value) if task_store_dir_value is not None else None
        )
        if params:
            names = ", ".join(sorted(params))
            raise ValueError(f"DfStopTaskTool got unknown params: {names}")
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=DfStopTaskAction,
                observation_type=DfStopTaskObservation,
                executor=DfStopTaskExecutor(
                    stop_url=stop_url,
                    headers=headers,
                    secret_headers=secret_headers,
                    timeout=timeout,
                    task_store_dir=task_store_dir,
                ),
                annotations=ToolAnnotations(
                    title="df_stop_task",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


register_tool("df_stop_task", DfStopTaskTool)
