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
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from pydantic import BaseModel, Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.tools.data_preparation.runner import (
    SUPPORTED_DATAFLOW_VERSION,
    build_dataflow_env,
    runtime_bundle_fingerprint,
    runtime_public_names,
    validate_managed_image_pipeline,
)
from openhands.tools.pyromind_dataset.definition import (
    PYROMIND_AGENT_STORAGE_ROOT,
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


RUNTIME_FILENAMES = (
    "df_logging.py",
    "generate_report.py",
    "image_utils.py",
    "preparation_runtime.py",
    "validate_prepared_data.py",
)
IMAGE_UTILS_API_VERSION = "1"
GPU_PRODUCT_FALLBACKS = ("NVIDIA-H100-NVL", "NVIDIA-H100-80GB-HBM3")
OutputSchema = Literal[
    "text",
    "dpo",
    "vision",
    "multiturn",
    "function_call",
    "quality_evaluation",
    "text2sql",
]

TOOL_DESCRIPTION = """\
Submit a DataFlow pipeline for asynchronous execution on Pyromind platform.

Call mode='full' only after the user confirms a successful local
df_run_pipeline Sample. The tool freezes the local script and shared runtime
in a per-run Storage directory. Set model_profile and output_schema explicitly
for new standard runs.

The tool creates a one-node CustomCommandNode workflow that:
1. Creates a venv and installs open-dataflow
2. Runs pipeline.py <input_path> <processed.jsonl>
3. Validates the selected canonical JSONL schema when output_schema is set
4. Always generates report.json, including failure and checkpoint state

Execution is asynchronous. A terminal Kafka callback resumes the
conversation when the workflow completes. After the callback, inspect
all Pyromind artifacts exclusively with `preview_dataset`; never use Terminal,
workspace file APIs, or local filesystem reads for these Storage paths:
- report.json: execution summary, LLM call stats, error samples
- failure.json / validation.json: detailed failure evidence when present
- llm_calls.jsonl: full per-call request/response log
- processed.jsonl: pipeline output data

To continue a failed run, use mode='resume' and resume_run_id. An unchanged
run reuses its frozen script. If pipeline, prompt, model, schema, or runtime
changes, locally regression-test the failed boundary and submit a structured
reuse_assessment; the tool records a new execution revision while preserving
the committed prefix. Use a new full run when prior output is not reusable.

The pipeline receives DF_API_KEY, DF_API_URL, DF_API_BASE_URL, DF_MODEL_NAME,
DF_LOG_DIR, DF_STATE_DIR, DF_RESUME, DF_EXECUTION_REVISION, and
DF_RUNTIME_FINGERPRINT. Managed vision pipelines import the staged
image_utils.py; text and legacy pipelines may continue using
preparation_runtime.py.
"""


# ---------------------------------------------------------------------------
# Action / Observation
# ---------------------------------------------------------------------------


class ReuseAssessment(BaseModel):
    """Agent-authored evidence that committed output remains reusable."""

    decision: Literal["compatible_resume"] = "compatible_resume"
    changed_dimensions: list[
        Literal["pipeline", "prompt", "model", "schema", "runtime"]
    ] = Field(default_factory=list)
    change_summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    verification_samples: list[str] = Field(min_length=1)
    verification_result: Literal["passed"]


class DfSubmitPipelineAction(Action):
    """Submit a DataFlow pipeline to Pyromind Studio."""

    script_path: str | None = Field(
        default=None,
        description=(
            "Local pipeline script for a new run or compatible resume, e.g."
            " '/workspace/conversations/<id>/public_data/data-preparation/pipeline.py'."
            " A strict resume may omit it and reuse the prior frozen script."
        ),
    )
    input_path: str = Field(description="Source data path in Pyromind Storage.")
    mode: Literal["full", "resume"] = Field(default="full")
    resume_run_id: uuid.UUID | None = Field(
        default=None,
        description="Existing run UUID. Required when mode='resume'.",
    )
    reuse_assessment: ReuseAssessment | None = Field(
        default=None,
        description=(
            "Required when a resume changes pipeline, prompt, model, schema, or "
            "runtime but the agent judges committed records reusable."
        ),
    )
    prompt_fingerprint: str | None = Field(
        default=None,
        description=(
            "Optional stable hash/version for prompts loaded outside the script."
        ),
    )
    model_profile: Literal["text", "vision"] | None = Field(
        default=None,
        description=(
            "Model profile for a new run. Resume inherits the prior profile when "
            "omitted."
        ),
    )
    output_schema: OutputSchema | None = Field(
        default=None,
        description=(
            "Canonical output schema: text, dpo, vision, multiturn, function_call, "
            "quality_evaluation, or text2sql. New data-preparation runs should "
            "always set this; None is retained only for legacy pipelines."
        ),
    )
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
        content.append(self.script_path or str(self.resume_run_id or "resume"))
        return content


class DfSubmitPipelineObservation(Observation):
    """Result of a platform pipeline submission."""

    status: str = Field(default="Unknown")
    task_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    output_dir: str | None = Field(default=None)
    resumed: bool = Field(default=False)
    execution_revision: int | None = Field(default=None)

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
        frozen_script_name: str = "pipeline.py",
        execution_revision: int = 1,
        model_profile: Literal["text", "vision"] = "text",
        output_schema: str | None = None,
        pipeline_fingerprint: str | None = None,
        prompt_fingerprint: str | None = None,
        model_fingerprint: str | None = None,
        runtime_fingerprint: str | None = None,
        runtime_dir_name: str = "",
        image_utils_api_version: str | None = None,
        resumed: bool = False,
        reuse_assessment: dict[str, Any] | None = None,
        status: str = "Pending",
    ):
        self.schema_version = 3
        self.task_id = task_id
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.output_dir = output_dir
        self.input_path = input_path
        self.script_path = script_path
        self.frozen_script_name = frozen_script_name
        self.execution_revision = execution_revision
        self.model_profile: Literal["text", "vision"] = model_profile
        self.output_schema = output_schema
        self.pipeline_fingerprint = pipeline_fingerprint
        self.prompt_fingerprint = prompt_fingerprint
        self.model_fingerprint = model_fingerprint
        self.runtime_fingerprint = runtime_fingerprint
        self.runtime_dir_name = runtime_dir_name
        self.image_utils_api_version = image_utils_api_version
        self.resumed = resumed
        self.reuse_assessment = reuse_assessment
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
            "frozen_script_name": self.frozen_script_name,
            "execution_revision": self.execution_revision,
            "model_profile": self.model_profile,
            "output_schema": self.output_schema,
            "pipeline_fingerprint": self.pipeline_fingerprint,
            "prompt_fingerprint": self.prompt_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "runtime_fingerprint": self.runtime_fingerprint,
            "runtime_dir_name": self.runtime_dir_name,
            "image_utils_api_version": self.image_utils_api_version,
            "resumed": self.resumed,
            "reuse_assessment": self.reuse_assessment,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataPreparationTaskAssociation:
        model_profile = data.get("model_profile", "text")
        if model_profile not in {"text", "vision"}:
            raise ValueError(f"Invalid persisted model_profile: {model_profile}")
        assoc = cls(
            task_id=data["task_id"],
            conversation_id=data["conversation_id"],
            run_id=data["run_id"],
            output_dir=data["output_dir"],
            input_path=data["input_path"],
            script_path=data["script_path"],
            frozen_script_name=data.get("frozen_script_name", "pipeline.py"),
            execution_revision=int(data.get("execution_revision", 1)),
            model_profile=cast(Literal["text", "vision"], model_profile),
            output_schema=data.get("output_schema"),
            pipeline_fingerprint=data.get("pipeline_fingerprint"),
            prompt_fingerprint=data.get("prompt_fingerprint"),
            model_fingerprint=data.get("model_fingerprint"),
            runtime_fingerprint=data.get("runtime_fingerprint"),
            runtime_dir_name=data.get("runtime_dir_name", ""),
            image_utils_api_version=data.get("image_utils_api_version"),
            resumed=bool(data.get("resumed", False)),
            reuse_assessment=data.get("reuse_assessment"),
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
        action: DfSubmitPipelineAction,
        conversation: BaseConversation | None = None,
    ) -> DfSubmitPipelineObservation:
        try:
            if conversation is None:
                raise ValueError("df_submit_pipeline requires an active conversation.")
            input_path = _normalize_storage_path(action.input_path, "input_path")
            task_store = self._task_store(conversation)
            resumed = action.mode == "resume"
            prior_run: DataPreparationTaskAssociation | None = None

            if resumed:
                if action.resume_run_id is None:
                    raise ValueError("resume_run_id is required when mode='resume'.")
                run_id = action.resume_run_id
                prior_run = task_store.get_by_run_id(str(run_id))
                if prior_run is None:
                    raise ValueError(
                        f"Cannot resume unknown data-preparation run {run_id}."
                    )
                if input_path != prior_run.input_path:
                    raise ValueError(
                        "input_path must match the original data-preparation run."
                    )
                output_dir = _normalize_storage_path(
                    prior_run.output_dir, "persisted output_dir"
                )
                execution_revision = prior_run.execution_revision + 1
                model_profile = action.model_profile or prior_run.model_profile
                output_schema = (
                    action.output_schema
                    if action.output_schema is not None
                    else prior_run.output_schema
                )
                prompt_fingerprint = (
                    action.prompt_fingerprint
                    if action.prompt_fingerprint is not None
                    else prior_run.prompt_fingerprint
                )
                if action.script_path is None:
                    script_path = prior_run.script_path
                    frozen_script_name = prior_run.frozen_script_name
                    pipeline_fingerprint = prior_run.pipeline_fingerprint
                    runtime_fingerprint = prior_run.runtime_fingerprint
                    runtime_dir_name = prior_run.runtime_dir_name
                    image_utils_api_version = prior_run.image_utils_api_version
                else:
                    script_path = _validate_local_pipeline(action.script_path)
                    pipeline_fingerprint = _file_sha256(Path(script_path))
                    frozen_script_name = f"pipeline-r{execution_revision}.py"
                    runtime_fingerprint = self._runtime_fingerprint()
                    runtime_dir_name = f"runtime-r{execution_revision}"
                    image_utils_api_version = IMAGE_UTILS_API_VERSION
                    if output_schema == "vision":
                        self._preflight_managed_image_pipeline(Path(script_path))
                llm_env = _build_llm_env(conversation, model_profile)
                model_fingerprint = _model_fingerprint(llm_env)
                changed_dimensions = _changed_dimensions(
                    prior_run=prior_run,
                    pipeline_fingerprint=pipeline_fingerprint,
                    prompt_fingerprint=prompt_fingerprint,
                    model_profile=model_profile,
                    model_fingerprint=model_fingerprint,
                    output_schema=output_schema,
                    runtime_fingerprint=runtime_fingerprint,
                )
                if changed_dimensions and action.reuse_assessment is None:
                    raise ValueError(
                        "Resume changes "
                        f"{', '.join(changed_dimensions)}. Provide reuse_assessment "
                        "after locally verifying that committed output is reusable, "
                        "or submit a new full run."
                    )
                if action.reuse_assessment is not None:
                    assessed = set(action.reuse_assessment.changed_dimensions)
                    missing_assessment = set(changed_dimensions) - assessed
                    if missing_assessment:
                        raise ValueError(
                            "reuse_assessment.changed_dimensions is missing: "
                            + ", ".join(sorted(missing_assessment))
                        )
                if action.script_path is not None:
                    self._stage_runtime_files(
                        output_dir,
                        conversation,
                        runtime_dir_name=runtime_dir_name,
                    )
                    self._stage_script(
                        script_path,
                        output_dir,
                        conversation,
                        frozen_script_name=frozen_script_name,
                    )
            else:
                if action.resume_run_id is not None:
                    raise ValueError("resume_run_id is only valid when mode='resume'.")
                if action.script_path is None:
                    raise ValueError("script_path is required for a new full run.")
                script_path = _validate_local_pipeline(action.script_path)
                run_id = uuid.uuid4()
                output_root = (
                    self._output_root
                    or f"{PYROMIND_AGENT_STORAGE_ROOT}/{conversation.id}/"
                    "data_preparation"
                )
                output_dir = str(PurePosixPath(output_root) / str(run_id))
                execution_revision = 1
                model_profile = action.model_profile or "text"
                output_schema = action.output_schema
                prompt_fingerprint = action.prompt_fingerprint
                frozen_script_name = "pipeline.py"
                pipeline_fingerprint = _file_sha256(Path(script_path))
                llm_env = _build_llm_env(conversation, model_profile)
                model_fingerprint = _model_fingerprint(llm_env)
                runtime_fingerprint = self._runtime_fingerprint()
                runtime_dir_name = "runtime-r1"
                image_utils_api_version = IMAGE_UTILS_API_VERSION
                if output_schema == "vision":
                    self._preflight_managed_image_pipeline(Path(script_path))
                changed_dimensions = []
                self._stage_runtime_files(
                    output_dir,
                    conversation,
                    runtime_dir_name=runtime_dir_name,
                )
                self._stage_script(
                    script_path,
                    output_dir,
                    conversation,
                    frozen_script_name=frozen_script_name,
                )

            command = _build_dataflow_command(
                input_path=input_path,
                output_dir=output_dir,
                llm_env=llm_env,
                convert_format=action.convert_format,
                frozen_script_name=frozen_script_name,
                resumed=resumed,
                execution_revision=execution_revision,
                runtime_dir_name=runtime_dir_name,
                runtime_fingerprint=runtime_fingerprint,
                image_utils_api_version=image_utils_api_version,
                output_schema=output_schema,
                reuse_assessment=(
                    action.reuse_assessment.model_dump()
                    if action.reuse_assessment is not None
                    else None
                ),
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
                resumed=resumed,
                execution_revision=execution_revision,
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
            frozen_script_name=frozen_script_name,
            execution_revision=execution_revision,
            model_profile=model_profile,
            output_schema=output_schema,
            pipeline_fingerprint=pipeline_fingerprint,
            prompt_fingerprint=prompt_fingerprint,
            model_fingerprint=model_fingerprint,
            runtime_fingerprint=runtime_fingerprint,
            runtime_dir_name=runtime_dir_name,
            image_utils_api_version=image_utils_api_version,
            resumed=resumed,
            reuse_assessment=(
                action.reuse_assessment.model_dump()
                if action.reuse_assessment is not None
                else None
            ),
            status=response.status,
        )
        try:
            task_store.save(association)
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
                resumed=resumed,
                execution_revision=execution_revision,
                is_error=True,
            )

        return DfSubmitPipelineObservation.from_text(
            text=(
                "DataFlow pipeline submitted. "
                f"task_id={task_id}, run_id={run_id}, "
                f"revision={execution_revision}, output_dir={output_dir}. "
                "While the job runs, call df_check_progress with this "
                "output_dir to report live progress, ETA, and recent output "
                "records. After the terminal callback, preview "
                f"{output_dir}/report.json, then processed.jsonl."
            ),
            status=response.status,
            task_id=task_id,
            run_id=str(run_id),
            output_dir=output_dir,
            resumed=resumed,
            execution_revision=execution_revision,
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
        *,
        runtime_dir_name: str,
    ) -> None:
        if self._runtime_dir is None:
            raise ValueError("DataFlow pipeline runtime_dir is not configured.")
        headers = self._resolved_storage_headers(conversation)
        target_dir = str(PurePosixPath(output_dir) / runtime_dir_name)
        for filename in RUNTIME_FILENAMES:
            local_path = self._runtime_dir / filename
            if not local_path.is_file():
                raise ValueError(f"DataFlow runtime file is missing: {local_path}")
            try:
                upload_local_file_to_pyromind(
                    local_path=local_path,
                    target_dir=target_dir,
                    storage_base_url=self._storage_base_url,
                    headers=headers,
                    timeout=float(self._timeout),
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"Failed to stage DataFlow runtime {filename}: {exc}"
                ) from exc

    def _runtime_fingerprint(self) -> str:
        if self._runtime_dir is None:
            raise ValueError("DataFlow pipeline runtime_dir is not configured.")
        return runtime_bundle_fingerprint(self._runtime_dir, RUNTIME_FILENAMES)

    def _preflight_managed_image_pipeline(self, pipeline: Path) -> None:
        if self._runtime_dir is None:
            raise ValueError("DataFlow pipeline runtime_dir is not configured.")
        image_utils = self._runtime_dir / "image_utils.py"
        validate_managed_image_pipeline(
            pipeline,
            runtime_public_names(image_utils),
        )

    def _stage_script(
        self,
        script_path: str,
        output_dir: str,
        conversation: BaseConversation,
        *,
        frozen_script_name: str,
    ) -> None:
        """Upload a local pipeline script under its immutable revision name."""
        headers = self._resolved_storage_headers(conversation)
        local = Path(script_path)
        try:
            with tempfile.TemporaryDirectory(prefix="data-preparation-script-") as tmp:
                target = Path(tmp) / frozen_script_name
                target.write_bytes(local.read_bytes())
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
            raise ValueError(f"DfSubmitPipelineTool got unknown params: {names}")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if output_root is not None:
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
    frozen_script_name: str = "pipeline.py",
    resumed: bool = False,
    execution_revision: int = 1,
    runtime_dir_name: str = "",
    runtime_fingerprint: str | None = None,
    image_utils_api_version: str | None = None,
    output_schema: str | None = None,
    reuse_assessment: dict[str, Any] | None = None,
) -> str:
    """Assemble the shell command executed inside the CustomCommandNode Pod."""
    pod_input = _pod_path(input_path)
    pod_output_dir = _pod_path(output_dir)
    pod_runtime_dir = (
        f"{pod_output_dir}/{runtime_dir_name}" if runtime_dir_name else pod_output_dir
    )
    frozen_script = f"{pod_output_dir}/{frozen_script_name}"
    output_file = f"{pod_output_dir}/processed.jsonl"
    venv_python = "/tmp/df-venv/bin/python"
    required_runtime_files = (
        RUNTIME_FILENAMES
        if runtime_dir_name
        else tuple(name for name in RUNTIME_FILENAMES if name != "image_utils.py")
    )

    # Environment variables for LLM access
    env_parts = []
    for key in (
        "DF_API_KEY",
        "DF_API_URL",
        "DF_API_BASE_URL",
        "DF_MODEL_NAME",
    ):
        value = llm_env.get(key, "")
        if value:
            env_parts.append(f"{key}={shlex.quote(value)}")
    env_parts.append(f"DF_LOG_DIR={shlex.quote(pod_output_dir)}")
    env_parts.append(f"DF_STATE_DIR={shlex.quote(pod_output_dir)}")
    env_parts.append(f"DF_RESUME={'1' if resumed else '0'}")
    env_parts.append(f"DF_EXECUTION_REVISION={execution_revision}")
    env_parts.append(f"PYTHONPATH={shlex.quote(pod_runtime_dir)}")
    if runtime_fingerprint:
        env_parts.append(f"DF_RUNTIME_FINGERPRINT={shlex.quote(runtime_fingerprint)}")
    if reuse_assessment is not None:
        env_parts.append(
            "DF_REUSE_ASSESSMENT_JSON="
            + shlex.quote(json.dumps(reuse_assessment, ensure_ascii=False))
        )
    env_prefix = " ".join(env_parts)

    # Pipeline arguments
    pipeline_args = [shlex.quote(pod_input), shlex.quote(output_file)]

    # Build the full command chain
    setup_steps = [
        "python3 -m venv /tmp/df-venv",
        "/tmp/df-venv/bin/pip install"
        " --use-deprecated=legacy-resolver "
        f"open-dataflow=={SUPPORTED_DATAFLOW_VERSION}",
        f"mkdir -p {shlex.quote(pod_output_dir)}",
        f"test -f {shlex.quote(frozen_script)}",
        *[
            f"test -f {shlex.quote(f'{pod_runtime_dir}/{filename}')}"
            for filename in required_runtime_files
        ],
    ]
    pipeline_step = (
        f"{env_prefix} {venv_python} {shlex.quote(frozen_script)}"
        f" {' '.join(pipeline_args)}"
    )
    validation_step = "validation_rc=0"
    if output_schema is not None:
        validator = f"{pod_runtime_dir}/validate_prepared_data.py"
        validation_report = f"{pod_output_dir}/validation.json"
        image_root_arg = (
            '--image-root "$image_root" ' if output_schema == "vision" else ""
        )
        validation_step = (
            "validation_rc=0; "
            'if [ "$pipeline_rc" -eq 0 ]; then '
            f"if [ -d {shlex.quote(pod_input)} ]; then "
            f"image_root={shlex.quote(pod_input)}; else "
            f"image_root=$(dirname {shlex.quote(pod_input)}); fi; "
            f"{venv_python} {shlex.quote(validator)} "
            f"{shlex.quote(output_file)} --schema {shlex.quote(output_schema)} "
            f"{image_root_arg}"
            f"--report {shlex.quote(validation_report)}"
            " || validation_rc=$?; fi"
        )
    report_step = (
        f"{venv_python} {shlex.quote(pod_runtime_dir)}/generate_report.py"
        f" --log-dir {shlex.quote(pod_output_dir)}"
        f' --pipeline-exit-code "$pipeline_rc"'
        f" --execution-revision {execution_revision}"
        f" --resumed {'true' if resumed else 'false'}"
        f" --runtime-dir-name {shlex.quote(runtime_dir_name)}"
    )
    if image_utils_api_version:
        report_step += " --image-utils-api-version " + shlex.quote(
            image_utils_api_version
        )
    if runtime_fingerprint:
        report_step += " --runtime-fingerprint " + shlex.quote(runtime_fingerprint)
    if reuse_assessment is not None:
        report_step += " --reuse-assessment-json " + shlex.quote(
            json.dumps(reuse_assessment, ensure_ascii=False)
        )
    report_step += " || true"
    final_step = (
        'if [ "$pipeline_rc" -ne 0 ]; then exit "$pipeline_rc"; fi; '
        'exit "$validation_rc"'
    )
    return (
        " && ".join([*setup_steps, pipeline_step])
        + f"; pipeline_rc=$?; {validation_step}; "
        + f"{report_step}; {final_step}"
    )


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


def _build_llm_env(
    conversation: BaseConversation,
    model_profile: Literal["text", "vision"] = "text",
) -> dict[str, str]:
    """Use the same model-profile resolver as local df_run_pipeline."""

    return build_dataflow_env(conversation, model_profile)


def _validate_local_pipeline(script_path: str) -> str:
    path = Path(script_path)
    if not path.is_file():
        raise ValueError(f"Pipeline script not found locally: {script_path}")
    if path.suffix.lower() != ".py":
        raise ValueError("script_path must point to a Python .py file.")
    return str(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_fingerprint(llm_env: dict[str, str]) -> str:
    nonsecret = {name: value for name, value in llm_env.items() if name != "DF_API_KEY"}
    serialized = json.dumps(nonsecret, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _changed_dimensions(
    *,
    prior_run: DataPreparationTaskAssociation,
    pipeline_fingerprint: str | None,
    prompt_fingerprint: str | None,
    model_profile: Literal["text", "vision"],
    model_fingerprint: str,
    output_schema: str | None,
    runtime_fingerprint: str | None,
) -> list[str]:
    changed: list[str] = []
    if (
        prior_run.pipeline_fingerprint is not None
        and pipeline_fingerprint != prior_run.pipeline_fingerprint
    ):
        changed.append("pipeline")
    if prompt_fingerprint != prior_run.prompt_fingerprint:
        changed.append("prompt")
    if model_profile != prior_run.model_profile or (
        prior_run.model_fingerprint is not None
        and model_fingerprint != prior_run.model_fingerprint
    ):
        changed.append("model")
    if output_schema != prior_run.output_schema:
        changed.append("schema")
    if runtime_fingerprint != prior_run.runtime_fingerprint:
        changed.append("runtime")
    return changed


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
