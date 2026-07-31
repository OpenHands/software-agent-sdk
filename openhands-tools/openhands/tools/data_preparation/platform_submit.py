"""Submit DataFlow pipeline scripts to Pyromind Studio for async execution.

Mirrors the data-cleaning platform submission pattern: upload script →
build CustomCommandNode workflow → submit → Kafka callback on completion.
Adds LLM call logging (df_logging.py) and report generation as runtime
files staged alongside the pipeline script.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Self, cast

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
from openhands.tools.pyromind_dataset.definition import (
    _default_storage_base_url,
    _resolve_conversation_headers,
    _resolve_secret_headers,
    upload_local_file_to_pyromind,
)
from openhands.tools.workflow.task_submission import (
    PYROMIND_WORKFLOW_AUTH_TOKEN_SECRET,
    create_workflow_api_client,
    submit_workflow_task,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


DEFAULT_PREPARATION_OUTPUT_ROOT = "/agentTest/data_preparation"
RUNTIME_FILENAMES = ("df_logging.py", "generate_report.py")
GPU_PRODUCT_FALLBACKS = ("NVIDIA-H100-NVL", "NVIDIA-H100-80GB-HBM3")

TOOL_DESCRIPTION = """\
Submit a DataFlow pipeline for asynchronous execution on Pyromind platform.

Call this only after the user has confirmed a successful local trial run
(`df_run_pipeline` with sample data). Pass the local pipeline script path
as `script_path`; the tool automatically uploads it (along with runtime
helpers) to a per-run output directory on Storage — no need to call
`upload_file_to_pyromind` separately.

The tool creates a one-node CustomCommandNode workflow that:
1. Creates a venv and installs open-dataflow
2. Runs the pipeline with LLM credentials injected as env vars
3. Generates an execution report (report.json) from LLM call logs

Execution is asynchronous. A terminal Kafka callback resumes the
conversation when the workflow completes. After the callback, inspect
results with `preview_dataset`:
- report.json: execution summary, LLM call stats, error samples
- llm_calls.jsonl: full per-call request/response log
- processed.jsonl: pipeline output data

The pipeline script contract is: `pipeline.py <input_path> <output_path>`.
LLM credentials are available as DF_API_KEY, DF_API_URL, DF_MODEL_NAME
environment variables. DF_LOG_DIR points to the output directory for
writing llm_calls.jsonl (use LoggingLLMServing from df_logging.py).
"""


# ---------------------------------------------------------------------------
# Action / Observation
# ---------------------------------------------------------------------------


class DfSubmitPipelineAction(Action):
    """Submit a DataFlow pipeline to Pyromind Studio."""

    script_path: str = Field(
        description=(
            "Local filesystem path of the pipeline script, e.g."
            " '/workspace/conversations/<id>/public_data/data-preparation/pipeline.py'."
            " The tool uploads it to the per-run output directory on Storage."
        )
    )
    input_path: str = Field(description="Source data path in Pyromind Storage.")
    convert_format: Literal["messages", "preference", "none"] = Field(
        default="none",
        description=(
            "Optional format conversion after pipeline completes."
            " 'none' skips conversion."
        ),
    )
    cpu: int = Field(default=4, ge=1, le=64)
    memory: int = Field(default=32, ge=1, le=256)

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Submit DataFlow pipeline: ", style="bold blue")
        content.append(self.script_path)
        return content


class DfSubmitPipelineObservation(Observation):
    """Result of a platform pipeline submission."""

    status: str = Field(default="Unknown")
    task_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    output_dir: str | None = Field(default=None)

    @property
    def visualize(self) -> Text:
        text = Text()
        style = "green" if self.status != "Failed" else "red"
        text.append(f"Pipeline submit: {self.status}\n", style=style)
        if self.run_id:
            text.append(f"run_id={self.run_id}\n")
        if self.output_dir:
            text.append(f"output_dir={self.output_dir}\n")
        text.append(self.text)
        return text


# ---------------------------------------------------------------------------
# Task Store
# ---------------------------------------------------------------------------

TASK_ASSOCIATION_DIRNAME = ".pyromind_data_preparation_tasks"


class DataPreparationTaskAssociation:
    """Durable link between a Studio task and its owning conversation."""

    def __init__(
        self,
        *,
        task_id: str,
        conversation_id: str,
        run_id: str,
        output_dir: str,
        input_path: str,
        script_path: str,
        status: str = "Pending",
    ):
        self.schema_version = 1
        self.task_id = task_id
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.output_dir = output_dir
        self.input_path = input_path
        self.script_path = script_path
        self.status = status
        self.submitted_at = datetime.now(UTC).isoformat()
        self.updated_at = self.submitted_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "output_dir": self.output_dir,
            "input_path": self.input_path,
            "script_path": self.script_path,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataPreparationTaskAssociation:
        assoc = cls(
            task_id=data["task_id"],
            conversation_id=data["conversation_id"],
            run_id=data["run_id"],
            output_dir=data["output_dir"],
            input_path=data["input_path"],
            script_path=data["script_path"],
            status=data.get("status", "Pending"),
        )
        assoc.submitted_at = data.get("submitted_at", assoc.submitted_at)
        assoc.updated_at = data.get("updated_at", assoc.updated_at)
        return assoc


class DataPreparationTaskStore:
    """Store task associations as atomically replaced JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, association: DataPreparationTaskAssociation) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(association.task_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(association.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def get(self, task_id: str) -> DataPreparationTaskAssociation | None:
        return self._read(self._path(task_id))

    def get_by_run_id(self, run_id: str) -> DataPreparationTaskAssociation | None:
        matches: list[DataPreparationTaskAssociation] = []
        try:
            for path in self.root.glob("*.json"):
                association = self._read(path)
                if association is not None and association.run_id == run_id:
                    matches.append(association)
        except OSError:
            return None
        return max(matches, key=lambda item: item.updated_at, default=None)

    def get_by_output_dir(
        self, output_dir: str
    ) -> DataPreparationTaskAssociation | None:
        normalized = output_dir.strip().rstrip("/")
        matches: list[DataPreparationTaskAssociation] = []
        try:
            for path in self.root.glob("*.json"):
                association = self._read(path)
                if association is None:
                    continue
                if association.output_dir.strip().rstrip("/") == normalized:
                    matches.append(association)
        except OSError:
            return None
        return max(matches, key=lambda item: item.updated_at, default=None)

    def _read(self, path: Path) -> DataPreparationTaskAssociation | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return DataPreparationTaskAssociation.from_dict(payload)
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            OSError,
            ValueError,
            KeyError,
        ):
            return None

    def _path(self, task_id: str) -> Path:
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class DfSubmitPipelineExecutor(
    ToolExecutor[DfSubmitPipelineAction, DfSubmitPipelineObservation]
):
    """Build and submit a DataFlow pipeline workflow to Pyromind Studio."""

    def __init__(
        self,
        *,
        env: str | None = None,
        cluster: str | None = None,
        output_root: str = DEFAULT_PREPARATION_OUTPUT_ROOT,
        headers: dict[str, str] | None = None,
        runtime_dir: str | None = None,
        storage_base_url: str | None = None,
        storage_headers: dict[str, str] | None = None,
        storage_secret_headers: dict[str, str] | None = None,
        task_store_dir: str | None = None,
        timeout: int = 30,
    ) -> None:
        self._env = env
        self._cluster = cluster
        self._output_root = _normalize_storage_path(output_root, "output_root")
        self._headers = dict(headers or {})
        self._runtime_dir = Path(runtime_dir) if runtime_dir else None
        self._storage_base_url = (
            storage_base_url or _default_storage_base_url()
        ).rstrip("/")
        self._storage_headers = dict(storage_headers or {})
        self._storage_secret_headers = dict(storage_secret_headers or {})
        self._task_store_dir = Path(task_store_dir) if task_store_dir else None
        self._timeout = timeout

    def __call__(
        self,
        action: DfSubmitPipelineAction,
        conversation: BaseConversation | None = None,
    ) -> DfSubmitPipelineObservation:
        try:
            if conversation is None:
                raise ValueError("df_submit_pipeline requires an active conversation.")
            script_path = action.script_path
            if not Path(script_path).is_file():
                raise ValueError(f"Pipeline script not found locally: {script_path}")
            if not script_path.endswith(".py"):
                raise ValueError("script_path must point to a Python .py file.")
            input_path = _normalize_storage_path(action.input_path, "input_path")

            run_id = uuid.uuid4()
            output_dir = str(PurePosixPath(self._output_root) / str(run_id))

            # Stage runtime files + pipeline script into output_dir
            self._stage_runtime_files(output_dir, conversation)
            self._stage_script(script_path, output_dir, conversation)

            # Build LLM env vars from conversation's agent LLM
            llm_env = _build_llm_env(conversation)

            command = _build_dataflow_command(
                input_path=input_path,
                output_dir=output_dir,
                llm_env=llm_env,
                convert_format=action.convert_format,
            )
            workflow = _build_dataflow_workflow(action, run_id, command)
        except ValueError as exc:
            return DfSubmitPipelineObservation.from_text(
                text=str(exc),
                status="Failed",
                is_error=True,
            )

        try:
            state = cast("ConversationState", conversation.state)
            auth_token = state.secret_registry.get_secret_value(
                PYROMIND_WORKFLOW_AUTH_TOKEN_SECRET
            )
            client = create_workflow_api_client(
                env=self._env,
                cluster=self._cluster,
                auth_token=auth_token,
                headers=self._headers,
                timeout=self._timeout,
            )
            last_exc: Exception | None = None
            for gpu_product in GPU_PRODUCT_FALLBACKS:
                workflow["nodes"][0]["data"]["config"]["gpu_product"] = gpu_product
                try:
                    response = submit_workflow_task(
                        client=client,
                        workflow=workflow,
                        name=str(workflow["name"]),
                        conversation_id=str(conversation.id),
                    )
                    task_id = response.task_id
                    break
                except Exception as exc:
                    last_exc = exc
            else:
                raise last_exc  # type: ignore[misc]
        except Exception as exc:
            return DfSubmitPipelineObservation.from_text(
                text=f"Failed to submit DataFlow pipeline: {exc}",
                status="Failed",
                run_id=str(run_id),
                output_dir=output_dir,
                is_error=True,
            )

        # Persist task association
        association = DataPreparationTaskAssociation(
            task_id=task_id,
            conversation_id=str(conversation.id),
            run_id=str(run_id),
            output_dir=output_dir,
            input_path=input_path,
            script_path=script_path,
            status=response.status,
        )
        try:
            self._task_store(conversation).save(association)
        except OSError as exc:
            return DfSubmitPipelineObservation.from_text(
                text=(
                    f"Studio accepted pipeline task {task_id}, but task "
                    f"persistence failed: {exc}"
                ),
                status=response.status,
                task_id=task_id,
                run_id=str(run_id),
                output_dir=output_dir,
                is_error=True,
            )

        return DfSubmitPipelineObservation.from_text(
            text=(
                "DataFlow pipeline submitted. "
                f"task_id={task_id}, run_id={run_id}, "
                f"output_dir={output_dir}. "
                "While the job runs, call df_check_progress with this "
                "output_dir to report live progress, ETA, and recent output "
                "records. After the terminal callback, preview "
                f"{output_dir}/report.json, then processed.jsonl."
            ),
            status=response.status,
            task_id=task_id,
            run_id=str(run_id),
            output_dir=output_dir,
        )

    def _task_store(self, conversation: BaseConversation) -> DataPreparationTaskStore:
        if self._task_store_dir is not None:
            return DataPreparationTaskStore(self._task_store_dir)
        workspace = cast(Any, conversation).workspace
        conversations_dir = Path(workspace.working_dir).resolve().parent
        return DataPreparationTaskStore(conversations_dir / TASK_ASSOCIATION_DIRNAME)

    def _stage_runtime_files(
        self,
        output_dir: str,
        conversation: BaseConversation,
    ) -> None:
        if self._runtime_dir is None:
            raise ValueError("DataFlow pipeline runtime_dir is not configured.")
        headers = self._resolved_storage_headers(conversation)
        for filename in RUNTIME_FILENAMES:
            local_path = self._runtime_dir / filename
            if not local_path.is_file():
                raise ValueError(f"DataFlow runtime file is missing: {local_path}")
            try:
                upload_local_file_to_pyromind(
                    local_path=local_path,
                    target_dir=output_dir,
                    storage_base_url=self._storage_base_url,
                    headers=headers,
                    timeout=float(self._timeout),
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"Failed to stage DataFlow runtime {filename}: {exc}"
                ) from exc

    def _stage_script(
        self,
        script_path: str,
        output_dir: str,
        conversation: BaseConversation,
    ) -> None:
        """Upload the local pipeline script into output_dir as pipeline.py."""
        headers = self._resolved_storage_headers(conversation)
        local = Path(script_path)
        target = local.parent / "pipeline.py"
        if local.name != "pipeline.py":
            target.write_bytes(local.read_bytes())
        try:
            upload_local_file_to_pyromind(
                local_path=target,
                target_dir=output_dir,
                storage_base_url=self._storage_base_url,
                headers=headers,
                timeout=float(self._timeout),
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Failed to stage pipeline script into {output_dir}: {exc}"
            ) from exc
        finally:
            if target != local:
                target.unlink(missing_ok=True)

    def _resolved_storage_headers(
        self,
        conversation: BaseConversation,
    ) -> dict[str, str]:
        headers = {"accept": "*/*", **self._storage_headers}
        headers.update(_resolve_conversation_headers(conversation))
        headers.update(
            _resolve_secret_headers(conversation, self._storage_secret_headers)
        )
        return headers


# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------


class DfSubmitPipelineTool(
    ToolDefinition[DfSubmitPipelineAction, DfSubmitPipelineObservation]
):
    """Tool definition for async DataFlow pipeline submissions."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[Self]:
        env_value = params.pop("env", None)
        env = str(env_value) if env_value is not None else None
        cluster_value = params.pop("cluster", None)
        cluster = str(cluster_value) if cluster_value is not None else None
        params.pop("current_user", None)
        output_root = str(params.pop("output_root", DEFAULT_PREPARATION_OUTPUT_ROOT))
        headers = _normalize_headers(params.pop("headers", None))
        runtime_dir_value = params.pop("runtime_dir", None)
        runtime_dir = str(runtime_dir_value) if runtime_dir_value is not None else None
        storage_base_url_value = params.pop("storage_base_url", None)
        storage_base_url = (
            str(storage_base_url_value) if storage_base_url_value is not None else None
        )
        storage_headers = _normalize_headers(params.pop("storage_headers", None))
        legacy_secret_headers = params.pop("secret_headers", None)
        storage_secret_headers = _normalize_headers(
            params.pop("storage_secret_headers", legacy_secret_headers)
        )
        env, cluster = _resolve_execution_target(env, cluster, headers)
        params.pop("endpoint_url", None)
        task_store_dir_value = params.pop("task_store_dir", None)
        task_store_dir = (
            str(task_store_dir_value) if task_store_dir_value is not None else None
        )
        timeout = int(params.pop("timeout", 30))
        if params:
            names = ", ".join(sorted(params))
            raise ValueError(f"DfSubmitPipelineTool got unknown params: {names}")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        _normalize_storage_path(output_root, "output_root")
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=DfSubmitPipelineAction,
                observation_type=DfSubmitPipelineObservation,
                executor=DfSubmitPipelineExecutor(
                    env=env,
                    cluster=cluster,
                    output_root=output_root,
                    headers=headers,
                    runtime_dir=runtime_dir,
                    storage_base_url=storage_base_url,
                    storage_headers=storage_headers,
                    storage_secret_headers=storage_secret_headers,
                    task_store_dir=task_store_dir,
                    timeout=timeout,
                ),
                annotations=ToolAnnotations(
                    title="df_submit_pipeline",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]


# ---------------------------------------------------------------------------
# Command / Workflow builders
# ---------------------------------------------------------------------------


def _build_dataflow_command(
    *,
    input_path: str,
    output_dir: str,
    llm_env: dict[str, str],
    convert_format: str,  # noqa: ARG001
) -> str:
    """Assemble the shell command executed inside the CustomCommandNode Pod."""
    pod_input = _pod_path(input_path)
    pod_output_dir = _pod_path(output_dir)
    frozen_script = f"{pod_output_dir}/pipeline.py"
    output_file = f"{pod_output_dir}/processed.jsonl"
    venv_python = "/tmp/df-venv/bin/python"

    # Environment variables for LLM access
    env_parts = []
    for key in ("DF_API_KEY", "DF_API_URL", "DF_MODEL_NAME"):
        value = llm_env.get(key, "")
        if value:
            env_parts.append(f"{key}={shlex.quote(value)}")
    env_parts.append(f"DF_LOG_DIR={shlex.quote(pod_output_dir)}")
    env_prefix = " ".join(env_parts)

    # Pipeline arguments
    pipeline_args = [shlex.quote(pod_input), shlex.quote(output_file)]

    # Build the full command chain
    steps = [
        "python3 -m venv /tmp/df-venv",
        "/tmp/df-venv/bin/pip install"
        " --use-deprecated=legacy-resolver open-dataflow==1.0.10",
        f"mkdir -p {shlex.quote(pod_output_dir)}",
        (
            f"{env_prefix} {venv_python} {shlex.quote(frozen_script)}"
            f" {' '.join(pipeline_args)}"
        ),
        (
            f"{venv_python} {shlex.quote(pod_output_dir)}/generate_report.py"
            f" --log-dir {shlex.quote(pod_output_dir)}"
        ),
    ]

    return " && ".join(steps)


def _build_dataflow_workflow(
    action: DfSubmitPipelineAction,
    run_id: uuid.UUID,
    command: str,
) -> dict[str, Any]:
    return {
        "id": str(run_id),
        "name": f"agent-data-prep-{str(run_id)[:8]}",
        "nodes": [
            {
                "id": "1",
                "type": "default",
                "position": {"x": 0, "y": 0},
                "data": {
                    "display_name": "DataFlow Pipeline",
                    "nodeType": "CustomCommandNode",
                    "config": {
                        "command": command,
                        "cpu": action.cpu,
                        "memory": action.memory,
                        "gpu_count": 0,
                        "gpu_product": GPU_PRODUCT_FALLBACKS[0],
                    },
                },
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_llm_env(conversation: BaseConversation) -> dict[str, str]:
    """Derive DataFlow LLM env vars from the conversation's agent LLM."""
    from pydantic import SecretStr

    state = cast("ConversationState", conversation.state)
    llm = state.agent.llm
    env: dict[str, str] = {}
    if llm.api_key is not None:
        if isinstance(llm.api_key, SecretStr):
            env["DF_API_KEY"] = llm.api_key.get_secret_value()
        else:
            env["DF_API_KEY"] = str(llm.api_key)
    base_url = (llm.base_url or "").rstrip("/")
    env["DF_API_URL"] = (
        f"{base_url}/chat/completions"
        if base_url
        else "https://api.openai.com/v1/chat/completions"
    )
    model = llm.model
    env["DF_MODEL_NAME"] = model.split("/", 1)[1] if "/" in model else model
    return env


def _normalize_headers(value: Any) -> dict[str, str] | None:
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("headers must be a dictionary when provided")
    return {str(name): str(header_value) for name, header_value in value.items()}


def _resolve_execution_target(
    env: str | None,
    cluster: str | None,
    headers: dict[str, str] | None,
) -> tuple[str | None, str | None]:
    routed_cluster = next(
        (
            value
            for name, value in (headers or {}).items()
            if name.lower() == "x-cluster"
        ),
        None,
    )
    if not routed_cluster:
        return env, cluster
    cluster_part, separator, env_part = routed_cluster.partition("#")
    resolved_cluster = cluster or cluster_part.strip() or None
    resolved_env = env or (env_part.strip().lower() if separator else "prod")
    return resolved_env, resolved_cluster


def _normalize_storage_path(value: str, field_name: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError(f"{field_name} must be a non-empty storage path.")
    if any(ord(character) < 32 for character in raw):
        raise ValueError(f"{field_name} contains control characters.")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        raise ValueError(f"{field_name} must not be the root or contain '..'.")
    return "/" + "/".join(parts)


def _pod_path(storage_path: str) -> str:
    return f"/target-workspace{storage_path}"


register_tool("df_submit_pipeline", DfSubmitPipelineTool)
