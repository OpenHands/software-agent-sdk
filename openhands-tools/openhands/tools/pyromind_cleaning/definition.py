"""Submit agent-authored dataset cleaning scripts to Pyromind Studio."""

from __future__ import annotations

import ast
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
from openhands.tools.pyromind_cleaning.task_store import (
    TASK_ASSOCIATION_DIRNAME,
    DatasetCleaningTaskAssociation,
    DatasetCleaningTaskStore,
)
from openhands.tools.pyromind_dataset.definition import (
    PYROMIND_AGENT_STORAGE_ROOT,
    _default_storage_base_url,
    _resolve_conversation_headers,
    _resolve_secret_headers,
    download_file_from_pyromind,
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


DEFAULT_GPU_PRODUCT = "NVIDIA-H100-NVL"
RUNTIME_FILENAMES = ("cleaning_utils.py", "validate_format.py")
MAX_CLEANING_SCRIPT_BYTES = 1024 * 1024


class RunDatasetCleaningAction(Action):
    """Submit a dataset cleaning script as a Studio workflow."""

    input_path: str = Field(
        description="Source dataset path in the user's Pyromind storage.",
    )
    script_path: str | None = Field(
        default=None,
        description=(
            "Uploaded Python cleaning script path for a new run. Resume uses the "
            "frozen script and may omit this field."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Optional maximum number of source records for this run.",
    )
    resume_run_id: uuid.UUID | None = Field(
        default=None,
        description=("Existing cleaning run UUID to resume. Omit to create a new run."),
    )
    cpu: int = Field(default=4, ge=1, le=64)
    memory: int = Field(default=32, ge=1, le=256)
    gpu_count: int = Field(default=0, ge=0, le=8)
    gpu_product: Literal[
        "NVIDIA-H100-NVL",
        "NVIDIA-L40S",
        "NVIDIA-H200",
        "NVIDIA-H100-80GB-HBM3",
    ] = Field(default=DEFAULT_GPU_PRODUCT)

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Run dataset cleaning: ", style="bold blue")
        content.append(self.input_path)
        return content


class RunDatasetCleaningObservation(Observation):
    """Initial result of a submitted dataset cleaning task."""

    status: str = Field(description="Initial Studio task status.")
    task_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    output_dir: str | None = Field(default=None)
    resumed: bool = Field(default=False)

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Dataset cleaning submission failed", style="bold red")
            return content
        content.append("Dataset cleaning submitted", style="bold green")
        if self.task_id:
            content.append(f"\ntask_id={self.task_id}")
        if self.output_dir:
            content.append(f"\noutput_dir={self.output_dir}")
        return content


TOOL_DESCRIPTION = """Run an uploaded dataset cleaner entirely on Pyromind.

Call this after previewing the source dataset, generating a cleaning script that
implements the CLI contract below, and uploading that script to Pyromind storage.
The tool creates a one-node CustomCommandNode workflow; do not construct or run
shell commands yourself.

The script must accept `--input`, `--output`, `--state-dir`, optional `--resume`,
and optional `--limit N`. `--output` is the concrete output.jsonl path, while
`--state-dir` is the run directory. A limit applies to source records considered
by that invocation. Output is homogeneous messages or text DPO preference JSONL.

The submission is asynchronous. A new run gets a unique result directory under
`/.pyromind-agent/<conversation_id>/data_cleaning/<run_id>`. The tool uploads
`cleaning_utils.py` and
`validate_format.py` beside the frozen `clean_script.py`, runs the cleaner, then
validates output.jsonl in the same Pod and merges validation into report.json.
The only generated artifacts are output.jsonl and report.json. Before creating a
new run it statically checks script syntax and explicit cleaning_utils imports; it
does not execute the script or read dataset content. Use `limit=3` for a sample
run. To continue an interrupted run, call this tool again with `resume_run_id`;
the platform reuses all frozen runtime files and passes `--resume`.

When the terminal workflow callback resumes the conversation, use the
`output_dir` returned by this tool. Inspect platform artifacts only with
`preview_dataset`: read report.json for validation, counters, errors, and
checkpoint state, then output.jsonl for cleaned rows. Never run or validate
Storage data locally.
"""


class RunDatasetCleaningExecutor(
    ToolExecutor[RunDatasetCleaningAction, RunDatasetCleaningObservation]
):
    """Build and submit a fixed CustomCommandNode cleaning workflow."""

    def __init__(
        self,
        *,
        env: str | None = None,
        cluster: str | None = None,
        output_root: str | None = None,
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
        self._output_root = (
            _normalize_storage_path(output_root, "output_root")
            if output_root is not None
            else None
        )
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
        action: RunDatasetCleaningAction,
        conversation: BaseConversation | None = None,
    ) -> RunDatasetCleaningObservation:
        try:
            if conversation is None:
                raise ValueError(
                    "run_dataset_cleaning requires an active conversation."
                )
            input_path = _normalize_storage_path(action.input_path, "input_path")
            output_root = (
                self._output_root
                or f"{PYROMIND_AGENT_STORAGE_ROOT}/{conversation.id}/data_cleaning"
            )
            uploaded_script_path = None
            if action.script_path is not None:
                uploaded_script_path = _normalize_storage_path(
                    action.script_path, "script_path"
                )
                if PurePosixPath(uploaded_script_path).suffix.lower() != ".py":
                    raise ValueError("script_path must point to a Python .py file.")
            task_store = self._task_store(conversation)
            run_id = action.resume_run_id or uuid.uuid4()
            resumed = action.resume_run_id is not None
            if resumed:
                prior_run = task_store.get_by_run_id(str(run_id))
                if prior_run is None:
                    raise ValueError(
                        f"Cannot resume unknown dataset cleaning run {run_id}."
                    )
                if input_path != prior_run.input_path:
                    raise ValueError(
                        "input_path must match the original dataset cleaning run."
                    )
                effective_script_path = prior_run.script_path
                output_dir = _normalize_storage_path(
                    prior_run.output_dir, "persisted output_dir"
                )
                if PurePosixPath(output_dir).name != str(run_id):
                    raise ValueError(
                        "Persisted dataset cleaning output directory does not "
                        "match resume_run_id."
                    )
            else:
                if uploaded_script_path is None:
                    raise ValueError("script_path is required for a new run.")
                effective_script_path = uploaded_script_path
                output_dir = str(PurePosixPath(output_root) / str(run_id))
                self._preflight_script(effective_script_path, conversation)
                self._stage_runtime_files(output_dir, conversation)
            command = _build_cleaning_command(
                input_path=input_path,
                script_path=effective_script_path,
                output_root=output_root,
                output_dir=output_dir,
                limit=action.limit,
                resumed=resumed,
            )
            workflow = _build_cleaning_workflow(action, run_id, command)
        except ValueError as exc:
            return RunDatasetCleaningObservation.from_text(
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
            response = submit_workflow_task(
                client=client,
                workflow=workflow,
                name=str(workflow["name"]),
                conversation_id=str(conversation.id),
            )
            task_id = response.task_id
        except Exception as exc:
            return RunDatasetCleaningObservation.from_text(
                text=f"Failed to submit dataset cleaning workflow: {exc}",
                status="Failed",
                run_id=str(run_id),
                output_dir=output_dir,
                resumed=resumed,
                is_error=True,
            )

        association = DatasetCleaningTaskAssociation(
            task_id=task_id,
            conversation_id=str(conversation.id),
            run_id=str(run_id),
            output_dir=output_dir,
            input_path=input_path,
            script_path=effective_script_path,
            limit=action.limit,
            resumed=resumed,
            status=response.status,
        )
        try:
            task_store.save(association)
        except OSError as exc:
            return RunDatasetCleaningObservation.from_text(
                text=(
                    f"Studio accepted dataset cleaning task {task_id}, but task "
                    f"association persistence failed: {exc}"
                ),
                status=response.status,
                task_id=task_id,
                run_id=str(run_id),
                output_dir=output_dir,
                resumed=resumed,
                is_error=True,
            )

        return RunDatasetCleaningObservation.from_text(
            text=(
                "Dataset cleaning workflow submitted. "
                f"task_id={task_id}, run_id={run_id}, output_dir={output_dir}. "
                "After the terminal callback, preview "
                f"{output_dir}/report.json, then output.jsonl."
            ),
            status=response.status,
            task_id=task_id,
            run_id=str(run_id),
            output_dir=output_dir,
            resumed=resumed,
        )

    def _task_store(self, conversation: BaseConversation) -> DatasetCleaningTaskStore:
        if self._task_store_dir is not None:
            return DatasetCleaningTaskStore(self._task_store_dir)
        workspace = cast(Any, conversation).workspace
        conversations_dir = Path(workspace.working_dir).resolve().parent
        return DatasetCleaningTaskStore(conversations_dir / TASK_ASSOCIATION_DIRNAME)

    def _stage_runtime_files(
        self,
        output_dir: str,
        conversation: BaseConversation,
    ) -> None:
        if self._runtime_dir is None:
            raise ValueError("Dataset cleaning runtime_dir is not configured.")
        headers = self._resolved_storage_headers(conversation)
        for filename in RUNTIME_FILENAMES:
            local_path = self._runtime_dir / filename
            if not local_path.is_file():
                raise ValueError(
                    f"Dataset cleaning runtime file is missing: {local_path}"
                )
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
                    f"Failed to stage dataset cleaning runtime {filename}: {exc}"
                ) from exc

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

    def _preflight_script(
        self,
        script_path: str,
        conversation: BaseConversation,
    ) -> None:
        if self._runtime_dir is None:
            raise ValueError("Dataset cleaning runtime_dir is not configured.")
        utils_path = self._runtime_dir / "cleaning_utils.py"
        if not utils_path.is_file():
            raise ValueError(f"Dataset cleaning runtime file is missing: {utils_path}")
        try:
            source_bytes = download_file_from_pyromind(
                storage_path=script_path,
                storage_base_url=self._storage_base_url,
                headers=self._resolved_storage_headers(conversation),
                timeout=float(self._timeout),
                max_bytes=MAX_CLEANING_SCRIPT_BYTES,
            )
            source = source_bytes.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(
                f"Failed to read dataset cleaning script for preflight: {exc}"
            ) from exc
        try:
            tree = ast.parse(source, filename=script_path)
            compile(tree, script_path, "exec")
        except SyntaxError as exc:
            location = f"line {exc.lineno}"
            if exc.offset is not None:
                location += f", column {exc.offset}"
            raise ValueError(
                f"Dataset cleaning script syntax error at {location}: {exc.msg}"
            ) from exc

        public_names = _runtime_public_names(utils_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "cleaning_utils" for alias in node.names
            ):
                raise ValueError(
                    "Dataset cleaning script must use explicit "
                    "from cleaning_utils import ... APIs."
                )
            if not isinstance(node, ast.ImportFrom) or node.module != "cleaning_utils":
                continue
            imported = {alias.name for alias in node.names}
            if "*" in imported:
                raise ValueError(
                    "Dataset cleaning script must import explicit cleaning_utils APIs."
                )
            unknown = sorted(imported - public_names)
            if unknown:
                raise ValueError(
                    "Dataset cleaning script imports unsupported cleaning_utils APIs: "
                    + ", ".join(unknown)
                )


class RunDatasetCleaningTool(
    ToolDefinition[RunDatasetCleaningAction, RunDatasetCleaningObservation]
):
    """Tool definition for asynchronous dataset cleaning submissions."""

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
        output_root_value = params.pop("output_root", None)
        output_root = str(output_root_value) if output_root_value is not None else None
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
            raise ValueError(f"RunDatasetCleaningTool got unknown params: {names}")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if output_root is not None:
            _normalize_storage_path(output_root, "output_root")
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=RunDatasetCleaningAction,
                observation_type=RunDatasetCleaningObservation,
                executor=RunDatasetCleaningExecutor(
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
                    title="run_dataset_cleaning",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]


def _normalize_headers(value: Any) -> dict[str, str] | None:
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("headers must be a dictionary when provided")
    return {str(name): str(header_value) for name, header_value in value.items()}


def _runtime_public_names(utils_path: Path) -> set[str]:
    try:
        tree = ast.parse(
            utils_path.read_text(encoding="utf-8"),
            filename=str(utils_path),
        )
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"Could not inspect cleaning_utils public API: {exc}") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            continue
        if node.value is None:
            raise ValueError("cleaning_utils __all__ must have a literal value")
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError) as exc:
            raise ValueError("cleaning_utils __all__ must be a literal list") from exc
        if not isinstance(value, list) or not all(
            isinstance(name, str) for name in value
        ):
            raise ValueError("cleaning_utils __all__ must contain only strings")
        return set(value)
    raise ValueError("cleaning_utils does not define a public __all__ contract")


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


def _build_cleaning_command(
    *,
    input_path: str,
    script_path: str,
    output_root: str,
    output_dir: str,
    limit: int | None,
    resumed: bool,
) -> str:
    pod_input = _pod_path(input_path)
    pod_output_root = _pod_path(output_root)
    pod_output_dir = _pod_path(output_dir)
    frozen_script = f"{pod_output_dir}/clean_script.py"
    cleaning_utils = f"{pod_output_dir}/cleaning_utils.py"
    validator = f"{pod_output_dir}/validate_format.py"
    output_file = f"{pod_output_dir}/output.jsonl"
    report_file = f"{pod_output_dir}/report.json"

    command_parts = [
        "python3",
        shlex.quote(frozen_script),
        "--input",
        shlex.quote(pod_input),
        "--output",
        shlex.quote(output_file),
        "--state-dir",
        shlex.quote(pod_output_dir),
    ]
    if resumed:
        command_parts.append("--resume")
        prefix = " && ".join(
            f"test -f {shlex.quote(path)}"
            for path in (frozen_script, cleaning_utils, validator)
        )
    else:
        prefix = " && ".join(
            [
                f"mkdir -p {shlex.quote(pod_output_root)}",
                f"mkdir -p {shlex.quote(pod_output_dir)}",
                (
                    f"cp {shlex.quote(_pod_path(script_path))} "
                    f"{shlex.quote(frozen_script)}"
                ),
            ]
        )
    if limit is not None:
        command_parts.extend(["--limit", str(limit)])
    validator_parts = [
        "python3",
        shlex.quote(validator),
        "--input",
        shlex.quote(output_file),
        "--report",
        shlex.quote(report_file),
    ]
    return f"{prefix} && {' '.join(command_parts)} && {' '.join(validator_parts)}"


def _build_cleaning_workflow(
    action: RunDatasetCleaningAction,
    run_id: uuid.UUID,
    command: str,
) -> dict[str, Any]:
    return {
        "id": str(run_id),
        "name": f"agent-data-clean-{str(run_id)[:8]}",
        "nodes": [
            {
                "id": "1",
                "type": "default",
                "position": {"x": 0, "y": 0},
                "data": {
                    "display_name": "Custom Command",
                    "nodeType": "CustomCommandNode",
                    "config": {
                        "command": command,
                        "cpu": action.cpu,
                        "memory": action.memory,
                        "gpu_count": action.gpu_count,
                        "gpu_product": action.gpu_product,
                    },
                },
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "timestamp": datetime.now(UTC).isoformat(),
    }


register_tool(RunDatasetCleaningTool.name, RunDatasetCleaningTool)
