"""Data preparation tools: download, run DataFlow pipelines, convert formats.

These tools implement the local data-preparation loop:

1. ``dataset_download`` materializes a HuggingFace dataset (or a sample of
   it) into the conversation workspace as JSONL.
2. ``df_run_pipeline`` executes an agent-authored DataFlow pipeline script in
   an isolated subprocess, injecting LLM credentials from the conversation's
   own LLM config (never hardcoded in the script).
3. ``df_convert`` turns processed JSONL into Pyromind-supported ``messages``,
   ``preference`` (DPO), embedded TRL vision SFT, or flat path-based vision
   SFT format, ready for upload.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Self

import httpx
from pydantic import Field, field_validator, model_validator
from rich.text import Text

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm.message import TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.workspace.workspace import LocalWorkspace
from openhands.tools.data_preparation.runner import (
    build_dataflow_env,
    check_dataflow_installed,
    resolve_dataflow_python,
    run_dataflow_python,
    summarize_dataflow_env,
)
from openhands.tools.utils import default_path_access_policy


logger = logging.getLogger(__name__)

_HF_ROWS_API = "https://datasets-server.huggingface.co"
_HF_ROWS_PAGE_SIZE = 100
_ROWS_API_MAX_ROWS = 10_000
_LOG_TAIL_CHARS = 6000


def _resolve_in_workspace(conversation: Any, path: str) -> Path:
    workspace = conversation.workspace
    if not isinstance(workspace, LocalWorkspace):
        raise ValueError(
            "This operation is only supported for local conversation workspaces."
        )
    workspace_dir = Path(workspace.working_dir).resolve()
    candidate = Path(path)

    # If path is absolute, check if it's within workspace_dir
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(workspace_dir)
        except ValueError as exc:
            raise ValueError(
                f"Path is outside the conversation workspace: {path}"
            ) from exc
        return resolved

    # If path is relative, resolve it relative to workspace_dir
    resolved = (workspace_dir / candidate).resolve()
    try:
        resolved.relative_to(workspace_dir)
    except ValueError as exc:
        raise ValueError(f"Path is outside the conversation workspace: {path}") from exc
    return resolved


def _resolve_workspace_path(conversation: Any, path: str) -> Path:
    """Resolve an existing readable file inside the workspace."""

    resolved = _resolve_in_workspace(conversation, path)
    workspace_dir = Path(conversation.workspace.working_dir).resolve()
    policy = default_path_access_policy(workspace_dir)
    if not policy.check(resolved, "read") or not resolved.is_file():
        raise ValueError(f"Missing or unreadable workspace file: {path}")
    return resolved


def _resolve_output_path(conversation: Any, path: str) -> Path:
    """Resolve a writable output path inside the workspace."""

    resolved = _resolve_in_workspace(conversation, path)
    workspace_dir = Path(conversation.workspace.working_dir).resolve()
    policy = default_path_access_policy(workspace_dir)
    if not policy.check(resolved, "write"):
        raise ValueError(f"Path is not writable by the agent: {path}")
    return resolved


# ---------------------------------------------------------------------------
# dataset_download
# ---------------------------------------------------------------------------


class DatasetDownloadAction(Action):
    dataset: str = Field(
        description=("HuggingFace dataset id, e.g. 'BytedTsinghua-SIA/DAPO-Math-17k'.")
    )
    output_path: str = Field(
        description=(
            "Workspace-relative destination JSONL path, e.g. "
            "'public_data/data-preparation/sample.jsonl'."
        )
    )
    split: str = Field(default="train", description="Dataset split to download.")
    config: str | None = Field(
        default=None,
        description="Dataset config/subset name, if the dataset has any.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Max rows to write. Omit to download the full split.",
    )
    hf_token_secret: str = Field(
        default="hf_token",
        description=(
            "Secret name resolving to a HuggingFace access token for gated or "
            "private datasets. Resolved via the secret manager; public datasets "
            "work without it."
        ),
    )

    @field_validator("output_path")
    @classmethod
    def _validate_output_path(cls, value: str) -> str:
        if not value.endswith(".jsonl"):
            raise ValueError("output_path must end with '.jsonl'")
        return value


class DatasetDownloadObservation(Observation):
    rows: int = Field(default=0, description="Rows written to the output file.")
    fields: list[str] = Field(default_factory=list, description="Dataset columns.")
    sample: list[dict[str, Any]] = Field(
        default_factory=list, description="First rows of the output for inspection."
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [
            TextContent(
                text=(
                    f"rows={self.rows} fields={self.fields} "
                    f"sample={json.dumps(self.sample, ensure_ascii=False)[:2000]}"
                )
            )
        ]

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append("Dataset Download\n", style="bold")
        text.append(f"  rows: {self.rows}\n")
        text.append(f"  fields: {self.fields}")
        return text


def _select_parquet_files(
    files: list[str], split: str, config: str | None
) -> list[str]:
    """Pick parquet files belonging to ``split``/``config`` from repo files."""

    parquet = [f for f in files if f.endswith(".parquet")]
    if not parquet:
        return []
    split_matches = [
        f
        for f in parquet
        if f"/{split}-" in f or f"/{split}/" in f or f.startswith(f"{split}-")
    ]
    if not split_matches:
        # Single-file datasets (e.g. 'data/dapo-math-17k.parquet') carry no
        # split marker in their paths at all — fall back to every parquet
        # file in the repo instead of returning nothing.
        logger.info(
            "No parquet files matched split '%s'; falling back to all %d "
            "parquet file(s) in the dataset repo.",
            split,
            len(parquet),
        )
        split_matches = parquet
    candidates = split_matches
    if config:
        config_matches = [f for f in candidates if f"/{config}/" in f]
        candidates = config_matches or candidates
    return sorted(candidates)


def _iter_parquet_rows(path: str):
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=512):
        yield from batch.to_pylist()


def _download_rows_via_parquet(
    dataset: str,
    *,
    split: str,
    config: str | None,
    limit: int | None,
    token: str | None,
) -> tuple[list[dict[str, Any]], int | None]:
    """Stream rows from the dataset's parquet conversion.

    Returns (rows, total_rows) where total_rows is None when unknown.
    """

    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    files = api.list_repo_files(dataset, repo_type="dataset")
    selected = _select_parquet_files(files, split, config)
    if not selected:
        raise ValueError(f"No parquet files found for split '{split}'.")

    total = 0
    rows: list[dict[str, Any]] = []
    for repo_file in selected:
        local = hf_hub_download(dataset, repo_file, repo_type="dataset", token=token)
        total += pq.ParquetFile(local).metadata.num_rows
        if limit is not None and len(rows) >= limit:
            continue
        for row in _iter_parquet_rows(local):
            if limit is not None and len(rows) >= limit:
                break
            rows.append(row)
    return rows, total


def _download_rows_via_api(
    dataset: str,
    *,
    split: str,
    config: str | None,
    limit: int | None,
    token: str | None,
) -> tuple[list[dict[str, Any]], int | None]:
    """Fallback: page through the HF datasets-server /rows endpoint."""

    if limit is None:
        raise ValueError(
            "This dataset has no parquet conversion on HuggingFace; "
            "falling back to the rows API requires an explicit `limit`."
        )
    limit = min(limit, _ROWS_API_MAX_ROWS)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=60.0, headers=headers) as client:
        if config is None:
            resp = client.get(f"{_HF_ROWS_API}/splits", params={"dataset": dataset})
            resp.raise_for_status()
            splits = resp.json().get("splits", [])
            matching = [s for s in splits if s.get("split") == split]
            if not matching:
                raise ValueError(f"Split '{split}' not found on datasets-server.")
            config = matching[0].get("config")
        rows: list[dict[str, Any]] = []
        total: int | None = None
        while len(rows) < limit:
            length = min(_HF_ROWS_PAGE_SIZE, limit - len(rows))
            resp = client.get(
                f"{_HF_ROWS_API}/rows",
                params={
                    "dataset": dataset,
                    "config": config,
                    "split": split,
                    "offset": len(rows),
                    "length": length,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            total = payload.get("num_rows_total")
            batch = [entry["row"] for entry in payload.get("rows", [])]
            if not batch:
                break
            rows.extend(batch)
    return rows, total


def _write_jsonl(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class DatasetDownloadExecutor(ToolExecutor):
    def __call__(
        self, action: DatasetDownloadAction, conversation: Any = None
    ) -> DatasetDownloadObservation:
        try:
            output = _resolve_output_path(conversation, action.output_path)
            token = None
            if action.hf_token_secret:
                token = conversation.state.secret_registry.get_secret_value(
                    action.hf_token_secret
                ) or os.environ.get("HF_TOKEN")
            try:
                rows, total = _download_rows_via_parquet(
                    action.dataset,
                    split=action.split,
                    config=action.config,
                    limit=action.limit,
                    token=token,
                )
            except Exception as parquet_exc:
                logger.info("Parquet download failed, trying rows API: %s", parquet_exc)
                rows, total = _download_rows_via_api(
                    action.dataset,
                    split=action.split,
                    config=action.config,
                    limit=action.limit,
                    token=token,
                )
            _write_jsonl(rows, output)
            fields = list(rows[0].keys()) if rows else []
            sample = [
                {key: str(value)[:200] for key, value in row.items()}
                for row in rows[:2]
            ]
            note = f"Downloaded {len(rows)} rows"
            if total:
                note += f" of ~{total} in split '{action.split}'"
            return DatasetDownloadObservation.from_text(
                text=f"{note} -> {output}",
                rows=len(rows),
                fields=fields,
                sample=sample,
            )
        except Exception as exc:
            return DatasetDownloadObservation.from_text(
                text=f"Dataset download failed: {exc}", is_error=True
            )


class DatasetDownloadTool(
    ToolDefinition[DatasetDownloadAction, DatasetDownloadObservation]
):
    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,  # noqa: ARG003
    ) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Download a HuggingFace dataset (full split or a `limit`-row "
                    "sample) into the conversation workspace as JSONL. Returns row "
                    "count, field names, and a small sample for inspection. Use "
                    "`limit` first to inspect data cheaply before any full run."
                ),
                action_type=DatasetDownloadAction,
                observation_type=DatasetDownloadObservation,
                executor=DatasetDownloadExecutor(),
            )
        ]


# ---------------------------------------------------------------------------
# df_run_pipeline
# ---------------------------------------------------------------------------


class DfRunPipelineAction(Action):
    pipeline_path: str = Field(
        description=(
            "Workspace-relative path of the DataFlow pipeline script to run, "
            "e.g. 'public_data/data-preparation/pipeline.py'."
        )
    )
    args: list[str] = Field(
        default_factory=list,
        description=(
            "Positional arguments forwarded to the pipeline script. Their meaning "
            "is defined by that script; multimodal templates use input path, "
            "output path, and optional limit."
        ),
    )
    timeout: int = Field(default=3600, ge=60, le=7200, description="Timeout seconds.")
    python: str | None = Field(
        default=None,
        description=(
            "Python interpreter override. Defaults to $DATAFLOW_PYTHON or the "
            "current interpreter. Must have `open-dataflow` installed."
        ),
    )


class DfRunPipelineObservation(Observation):
    exit_code: int = Field(default=-1)
    stdout_tail: str = Field(default="")
    stderr_tail: str = Field(default="")

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [
            TextContent(
                text=(
                    f"exit_code={self.exit_code}\n"
                    f"--- stdout (tail) ---\n{self.stdout_tail}\n"
                    f"--- stderr (tail) ---\n{self.stderr_tail}"
                )
            )
        ]

    @property
    def visualize(self) -> Text:
        text = Text()
        style = "green" if self.exit_code == 0 else "red"
        text.append(f"Pipeline finished (exit {self.exit_code})\n", style=style)
        tail = (self.stdout_tail or self.stderr_tail)[-500:]
        if tail:
            text.append(tail)
        return text


class DfRunPipelineExecutor(ToolExecutor):
    def __call__(
        self, action: DfRunPipelineAction, conversation: Any = None
    ) -> DfRunPipelineObservation:
        try:
            pipeline = _resolve_workspace_path(conversation, action.pipeline_path)
        except Exception as exc:
            return DfRunPipelineObservation.from_text(
                text=f"Invalid pipeline path: {exc}", is_error=True
            )

        python = resolve_dataflow_python(action.python)
        ok, detail = check_dataflow_installed(python)
        if not ok:
            return DfRunPipelineObservation.from_text(
                text=(
                    f"DataFlow is not installed for interpreter `{python}`.\n"
                    "Install it with `uv pip install open-dataflow` (preferably "
                    "in a dedicated venv) or set DATAFLOW_PYTHON to an "
                    f"interpreter that has it.\nImport check: {detail or 'failed'}"
                ),
                is_error=True,
            )

        try:
            env_extra = build_dataflow_env(conversation)
        except ValueError as exc:
            return DfRunPipelineObservation.from_text(
                text=f"Invalid DataFlow model configuration: {exc}",
                is_error=True,
            )
        config_summary = summarize_dataflow_env(env_extra)
        rc, stdout, stderr = run_dataflow_python(
            python,
            [str(pipeline), *action.args],
            cwd=str(pipeline.parent),
            env_extra=env_extra,
            timeout=action.timeout,
        )
        return DfRunPipelineObservation.from_text(
            text=(
                f"DataFlow model: {config_summary}\n"
                f"exit_code={rc}\n"
                f"--- stdout (tail) ---\n{stdout[-_LOG_TAIL_CHARS:]}\n"
                f"--- stderr (tail) ---\n{stderr[-_LOG_TAIL_CHARS:]}"
            ),
            is_error=rc != 0,
            exit_code=rc,
            stdout_tail=stdout[-_LOG_TAIL_CHARS:],
            stderr_tail=stderr[-_LOG_TAIL_CHARS:],
        )


class DfRunPipelineTool(ToolDefinition[DfRunPipelineAction, DfRunPipelineObservation]):
    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,  # noqa: ARG003
    ) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Run an agent-authored DataFlow script in an isolated "
                    "subprocess. The script reads server-provided DF_* model "
                    "settings and returns only textual status/output; image content "
                    "is read by the DataFlow VLM. Relative arguments resolve from "
                    "the pipeline directory."
                ),
                action_type=DfRunPipelineAction,
                observation_type=DfRunPipelineObservation,
                executor=DfRunPipelineExecutor(),
            )
        ]

    @classmethod
    def is_usable(cls) -> bool:
        ok, _ = check_dataflow_installed(resolve_dataflow_python())
        return ok


# ---------------------------------------------------------------------------
# df_convert
# ---------------------------------------------------------------------------


class DfConvertAction(Action):
    input_path: str = Field(
        description=(
            "Workspace-relative processed JSONL path, e.g."
            " 'public_data/data-preparation/processed.jsonl'."
        )
    )
    output_path: str = Field(
        description=(
            "Workspace-relative output path. Use .jsonl for messages/preference "
            "or .parquet for trl_vision_sft/vision_sft_flat."
        )
    )
    format: Literal[
        "messages",
        "preference",
        "trl_vision_sft",
        "vision_sft_flat",
    ] = Field(
        default="messages",
        description=(
            "'messages' for text SFT, 'preference' for DPO pairs, or "
            "'trl_vision_sft' for embedded multimodal conversational SFT, or "
            "'vision_sft_flat' for path-based flat visual SFT."
        ),
    )
    text_field: str = Field(default="text", description="User-turn field.")
    reasoning_field: str = Field(
        default="reasoning_content",
        description="Optional CoT field prepended to the assistant turn.",
    )
    answer_field: str = Field(
        default="final_answer", description="Assistant answer field."
    )
    system_prompt: str | None = Field(
        default=None, description="Optional system message for messages format."
    )
    chosen_field: str = Field(default="chosen")
    rejected_field: str = Field(default="rejected")
    prompt_field: str = Field(
        default="training_prompt",
        description="User prompt field for trl_vision_sft.",
    )
    response_field: str = Field(
        default="training_response",
        description="Assistant response field for trl_vision_sft.",
    )
    images_field: str = Field(
        default="image_paths",
        description="Ordered image path list field for trl_vision_sft.",
    )
    image_labels_field: str = Field(
        default="image_labels",
        description="Optional ordered image role list field for trl_vision_sft.",
    )
    id_field: str = Field(
        default="sample_id",
        description="Unique sample identifier field for vision_sft_flat.",
    )
    system_prompt_field: str = Field(
        default="training_system_prompt",
        description="System prompt field for vision_sft_flat.",
    )

    @field_validator("input_path")
    @classmethod
    def _validate_input_jsonl(cls, value: str) -> str:
        if not value.endswith(".jsonl"):
            raise ValueError("input_path must end with '.jsonl'")
        return value

    @model_validator(mode="after")
    def _validate_output_suffix(self) -> Self:
        expected = (
            ".parquet"
            if self.format in {"trl_vision_sft", "vision_sft_flat"}
            else ".jsonl"
        )
        if not self.output_path.endswith(expected):
            raise ValueError(
                f"output_path must end with '{expected}' for format '{self.format}'"
            )
        return self


class DfConvertObservation(Observation):
    converted: int = Field(default=0)
    skipped: int = Field(default=0)
    output_path: str = Field(default="")
    columns: list[str] = Field(default_factory=list)
    column_schema: dict[str, str] = Field(default_factory=dict)
    image_path_mode: str | None = Field(default=None)

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [
            TextContent(
                text=(
                    f"converted={self.converted} skipped={self.skipped} "
                    f"output_path={self.output_path} "
                    f"column_schema={self.column_schema}"
                    + (
                        f" image_path_mode={self.image_path_mode}"
                        if self.image_path_mode
                        else ""
                    )
                )
            )
        ]

    @property
    def visualize(self) -> Text:
        return Text(
            f"Converted {self.converted} records ({self.skipped} skipped)"
            f" -> {self.output_path}; columns={self.columns}"
        )


def _to_messages(
    record: dict[str, Any], action: DfConvertAction
) -> dict[str, Any] | None:
    text = record.get(action.text_field)
    answer = record.get(action.answer_field)
    if not text or not answer:
        return None
    reasoning = record.get(action.reasoning_field)
    parts = [part for part in (reasoning, answer) if part]
    messages = []
    if action.system_prompt:
        messages.append({"role": "system", "content": action.system_prompt})
    messages.append({"role": "user", "content": text})
    messages.append({"role": "assistant", "content": "\n\n".join(parts)})
    return {"messages": messages}


def _to_preference(
    record: dict[str, Any], action: DfConvertAction
) -> dict[str, Any] | None:
    prompt = record.get("prompt") or record.get(action.text_field)
    chosen = record.get(action.chosen_field)
    rejected = record.get(action.rejected_field)
    if not prompt or not chosen or not rejected:
        return None
    return {"prompt": prompt, "chosen": chosen, "rejected": rejected}


def _stringify_training_value(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _validate_tagged_training_response(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("training response must be a string")
    if (
        value.count("<think>") != 1
        or value.count("</think>") != 1
        or value.count("<answer>") != 1
        or value.count("</answer>") != 1
    ):
        raise ValueError("training response must contain each required tag once")
    prefix = "<think>"
    separator = "</think>\n\n<answer>"
    suffix = "</answer>"
    if not value.startswith(prefix) or not value.endswith(suffix):
        raise ValueError("training response has invalid tag order")
    body = value[len(prefix) : -len(suffix)]
    if separator not in body:
        raise ValueError("training response has invalid tag order")
    think, answer = body.split(separator, maxsplit=1)
    if not think.strip() or not answer.strip():
        raise ValueError("training response think and answer must be non-empty")
    return value


def _resolve_training_image(
    source: Path,
    raw_path: Any,
    workspace_root: Path,
) -> dict[str, Any]:
    from PIL import Image

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("image path must be a non-empty string")
    candidate = Path(raw_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (source.parent / candidate).resolve()
    )
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"image path is outside the workspace: {raw_path}") from exc
    if not resolved.is_file():
        raise ValueError(f"missing image: {raw_path}")
    if resolved.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError(f"unsupported image format: {raw_path}")
    payload = resolved.read_bytes()
    with Image.open(BytesIO(payload)) as image:
        image.verify()
    return {"bytes": payload, "path": resolved.name}


def _validate_flat_training_image(
    source: Path,
    raw_path: Any,
    workspace_root: Path,
) -> str:
    from PIL import Image

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("image path must be a non-empty string")
    candidate = Path(raw_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (source.parent / candidate).resolve()
    )
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"image path is outside the workspace: {raw_path}") from exc
    if not resolved.is_file():
        raise ValueError(f"missing image: {raw_path}")
    with Image.open(resolved) as image:
        image.verify()
    return raw_path


def _to_trl_vision_sft(
    record: dict[str, Any],
    action: DfConvertAction,
    *,
    source: Path,
    workspace_root: Path,
) -> dict[str, Any] | None:
    prompt = _stringify_training_value(record.get(action.prompt_field))
    response = _stringify_training_value(record.get(action.response_field))
    raw_images = record.get(action.images_field)
    if not prompt or not response or not isinstance(raw_images, list) or not raw_images:
        return None

    raw_labels = record.get(action.image_labels_field)
    if raw_labels is None:
        labels = [""] * len(raw_images)
    elif isinstance(raw_labels, list) and len(raw_labels) == len(raw_images):
        labels = [
            str(label).strip() if label is not None else "" for label in raw_labels
        ]
    else:
        return None

    images = [
        _resolve_training_image(source, image_path, workspace_root)
        for image_path in raw_images
    ]
    user_content: list[dict[str, str | None]] = [{"type": "text", "text": prompt}]
    for label in labels:
        if label:
            user_content.append({"type": "text", "text": label})
        user_content.append({"type": "image", "text": None})

    messages: list[dict[str, Any]] = []
    if action.system_prompt:
        messages.append(
            {
                "role": "system",
                "content": [{"type": "text", "text": action.system_prompt}],
            }
        )
    messages.extend(
        [
            {"role": "user", "content": user_content},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": response}],
            },
        ]
    )
    return {"messages": messages, "images": images}


def _trl_vision_schema():
    import pyarrow as pa

    image = pa.struct(
        [
            pa.field("bytes", pa.binary()),
            pa.field("path", pa.string()),
        ]
    )
    content = pa.struct(
        [
            pa.field("type", pa.string()),
            pa.field("text", pa.string()),
        ]
    )
    message = pa.struct(
        [
            pa.field("role", pa.string()),
            pa.field("content", pa.list_(content)),
        ]
    )
    features = {
        "images": {"feature": {"_type": "Image"}, "_type": "List"},
        "messages": {
            "feature": {
                "role": {"dtype": "string", "_type": "Value"},
                "content": {
                    "feature": {
                        "type": {"dtype": "string", "_type": "Value"},
                        "text": {"dtype": "string", "_type": "Value"},
                    },
                    "_type": "List",
                },
            },
            "_type": "List",
        },
    }
    metadata = {
        b"huggingface": json.dumps(
            {"info": {"features": features}},
            separators=(",", ":"),
        ).encode()
    }
    return pa.schema(
        [
            pa.field("messages", pa.list_(message)),
            pa.field("images", pa.list_(image)),
        ],
        metadata=metadata,
    )


def _convert_trl_vision_records(
    source: Path,
    target: Path,
    action: DfConvertAction,
    *,
    workspace_root: Path,
) -> tuple[int, int]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    skipped = 0
    with source.open("r", encoding="utf-8") as reader:
        for line in reader:
            if not line.strip():
                continue
            record = json.loads(line)
            out = _to_trl_vision_sft(
                record,
                action,
                source=source,
                workspace_root=workspace_root,
            )
            if out is None:
                skipped += 1
                continue
            rows.append(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=_trl_vision_schema())
    pq.write_table(table, target)
    reloaded = pq.read_table(target)
    if reloaded.num_rows != len(rows):
        raise ValueError(
            "Parquet verification failed: "
            f"wrote {len(rows)} rows but reloaded {reloaded.num_rows}"
        )
    return len(rows), skipped


def _vision_sft_flat_schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("image_path", pa.string(), nullable=False),
            pa.field("images", pa.list_(pa.string()), nullable=False),
            pa.field("system_prompt", pa.string(), nullable=False),
            pa.field("user_prompt", pa.string(), nullable=False),
            pa.field("gt", pa.string(), nullable=False),
        ]
    )


def _required_string_field(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _convert_vision_sft_flat_records(
    source: Path,
    target: Path,
    action: DfConvertAction,
    *,
    workspace_root: Path,
) -> tuple[int, int]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    sample_ids: set[str] = set()
    with source.open("r", encoding="utf-8") as reader:
        for line_number, line in enumerate(reader, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: record must be an object")

            sample_id = _required_string_field(record, action.id_field)
            if sample_id in sample_ids:
                raise ValueError(f"duplicate {action.id_field}: {sample_id}")
            sample_ids.add(sample_id)

            raw_images = record.get(action.images_field)
            if not isinstance(raw_images, list) or not raw_images:
                raise ValueError(
                    f"line {line_number}: {action.images_field} must be a "
                    "non-empty list"
                )
            images = [
                _validate_flat_training_image(source, raw_path, workspace_root)
                for raw_path in raw_images
            ]
            response = _validate_tagged_training_response(
                record.get(action.response_field)
            )
            rows.append(
                {
                    "id": sample_id,
                    "image_path": images[0],
                    "images": images,
                    "system_prompt": _required_string_field(
                        record, action.system_prompt_field
                    ),
                    "user_prompt": _required_string_field(record, action.prompt_field),
                    "gt": response,
                }
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    expected_columns = [
        "id",
        "image_path",
        "images",
        "system_prompt",
        "user_prompt",
        "gt",
    ]
    table = pa.Table.from_pylist(rows, schema=_vision_sft_flat_schema())
    pq.write_table(table, target)
    reloaded = pq.read_table(target)
    if reloaded.num_rows != len(rows):
        raise ValueError(
            "Parquet verification failed: "
            f"wrote {len(rows)} rows but reloaded {reloaded.num_rows}"
        )
    if reloaded.column_names != expected_columns:
        raise ValueError(
            "Parquet verification failed: "
            f"expected columns {expected_columns}, got {reloaded.column_names}"
        )
    return len(rows), 0


def _convert_records(
    source: Path, target: Path, action: DfConvertAction
) -> tuple[int, int]:
    converted = skipped = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        source.open("r", encoding="utf-8") as reader,
        target.open("w", encoding="utf-8") as writer,
    ):
        for line in reader:
            if not line.strip():
                continue
            record = json.loads(line)
            out = (
                _to_messages(record, action)
                if action.format == "messages"
                else _to_preference(record, action)
            )
            if out is None:
                skipped += 1
                continue
            writer.write(json.dumps(out, ensure_ascii=False) + "\n")
            converted += 1
    return converted, skipped


class DfConvertExecutor(ToolExecutor):
    def __call__(
        self, action: DfConvertAction, conversation: Any = None
    ) -> DfConvertObservation:
        try:
            source = _resolve_workspace_path(conversation, action.input_path)
            target = _resolve_output_path(conversation, action.output_path)
            if action.format in {"trl_vision_sft", "vision_sft_flat"}:
                workspace_root = Path(conversation.workspace.working_dir).resolve()
                if action.format == "trl_vision_sft":
                    converted, skipped = _convert_trl_vision_records(
                        source,
                        target,
                        action,
                        workspace_root=workspace_root,
                    )
                else:
                    converted, skipped = _convert_vision_sft_flat_records(
                        source,
                        target,
                        action,
                        workspace_root=workspace_root,
                    )
            else:
                converted, skipped = _convert_records(source, target, action)
            columns_by_format = {
                "messages": ["messages"],
                "preference": ["prompt", "chosen", "rejected"],
                "trl_vision_sft": ["messages", "images"],
                "vision_sft_flat": [
                    "id",
                    "image_path",
                    "images",
                    "system_prompt",
                    "user_prompt",
                    "gt",
                ],
            }
            columns = columns_by_format[action.format]
            schemas_by_format = {
                "messages": {"messages": "list<message>"},
                "preference": {
                    "prompt": "string",
                    "chosen": "string",
                    "rejected": "string",
                },
                "trl_vision_sft": {
                    "messages": "list<message>",
                    "images": "list<image>",
                },
                "vision_sft_flat": {
                    "id": "string",
                    "image_path": "string",
                    "images": "list<string>",
                    "system_prompt": "string",
                    "user_prompt": "string",
                    "gt": "string",
                },
            }
            column_schema = schemas_by_format[action.format]
            image_path_mode = (
                "ordered paths (not embedded)"
                if action.format == "vision_sft_flat"
                else None
            )
            return DfConvertObservation.from_text(
                text=(
                    f"Converted {converted} records -> {target} "
                    f"({skipped} skipped as incomplete); "
                    f"column_schema={column_schema}"
                    + (
                        f"; image_path_mode={image_path_mode}"
                        if image_path_mode
                        else ""
                    )
                ),
                converted=converted,
                skipped=skipped,
                output_path=str(target),
                columns=columns,
                column_schema=column_schema,
                image_path_mode=image_path_mode,
            )
        except Exception as exc:
            return DfConvertObservation.from_text(
                text=f"Conversion failed: {exc}", is_error=True
            )


class DfConvertTool(ToolDefinition[DfConvertAction, DfConvertObservation]):
    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,  # noqa: ARG003
    ) -> Sequence[Self]:
        return [
            cls(
                description=(
                    "Convert a processed JSONL into a Pyromind-supported training "
                    "format: 'messages' (SFT, optional reasoning_content CoT + "
                    "final_answer merged into the assistant turn) or 'preference' "
                    "(DPO prompt/chosen/rejected), or 'trl_vision_sft' "
                    "(legacy multimodal messages plus embedded images), or "
                    "'vision_sft_flat' (id, ordered image paths, prompts, and "
                    "tagged ground truth in Parquet). Conversion verifies image "
                    "decoding and reloads the final artifact. Upload the result with "
                    "upload_file_to_pyromind."
                ),
                action_type=DfConvertAction,
                observation_type=DfConvertObservation,
                executor=DfConvertExecutor(),
            )
        ]


register_tool("dataset_download", DatasetDownloadTool)
register_tool("df_run_pipeline", DfRunPipelineTool)
register_tool("df_convert", DfConvertTool)
