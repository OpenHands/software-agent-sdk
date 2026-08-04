"""Definitions of the preview_dataset and upload_file_to_pyromind tools."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx
from pydantic import AliasChoices, Field
from rich.text import Text

from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.tools.utils import default_path_access_policy


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


PRE_STORAGE_API_BASE_URL = "https://pre-api-portal.pyromind.ai/storage_api"
PROD_STORAGE_API_BASE_URL = "https://api-portal.pyromind.ai/storage_api"
PYROMIND_STORAGE_AUTH_COOKIE_SECRET = "PYROMIND_STORAGE_AUTH_COOKIE"
PYROMIND_STORAGE_HEADERS_STATE_KEY = "pyromind_storage_headers"
DEFAULT_UPLOAD_TARGET_DIR = "/agentTest"

_PROD_APP_ENVS = {"prod", "production", "online"}
_SMALL_FILE_THRESHOLD = 10 * 1024
_LARGE_FILE_RANGE_BYTES = 5 * 1024
_LARGE_FILE_RANGE_COUNT = 3
_DEFAULT_PREVIEW_BYTES = _LARGE_FILE_RANGE_BYTES * _LARGE_FILE_RANGE_COUNT
_MAX_PREVIEW_BYTES = _DEFAULT_PREVIEW_BYTES
_MAX_REQUESTED_SAMPLES = 100
_DELIMITED_HEADER_BYTES = 4096
_MAX_SAMPLE_STRING_CHARS = 2000
_TEXT_PREVIEW_SUFFIXES = {".txt", ".md", ".log", ".sh"}
_SUPPORTED_PREVIEW_SUFFIXES = {
    ".jsonl",
    ".json",
    ".csv",
    ".tsv",
    *_TEXT_PREVIEW_SUFFIXES,
}
_VISION_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".mp4",
    ".mov",
    ".avi",
    ".webm",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VISION_FIELD_MARKERS = ("image", "video", "vision")
_DEFAULT_SAMPLE_COUNT = 3
_MAX_SAMPLE_FILES = 100
_MAX_SAMPLE_FILE_BYTES = 25 * 1024 * 1024
_MAX_SAMPLE_TOTAL_BYTES = 100 * 1024 * 1024
_MAX_SAMPLE_DIRECTORY_DEPTH = 6
_MAX_VISION_PREVIEW_IMAGES = 12
_MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
_VISION_RETRYABLE_STATUS_CODES = {408, 409, 425, 429}
_MAX_DIRECTORY_CHILD_SAMPLES = 3


@dataclass(frozen=True)
class _PreviewChunk:
    content: bytes
    starts_at_zero: bool
    ends_at_eof: bool


@dataclass(frozen=True)
class _StorageFileInfo:
    path: str
    name: str
    size: int | None
    last_modified: str | None
    is_dir: bool = False


def _default_storage_base_url() -> str:
    app_env = os.getenv("APP_ENV", "dev").strip().lower()
    if app_env in _PROD_APP_ENVS:
        return PROD_STORAGE_API_BASE_URL
    return PRE_STORAGE_API_BASE_URL


# ---------------------------------------------------------------------------
# preview_dataset
# ---------------------------------------------------------------------------


class PreviewDatasetAction(Action):
    """Preview a dataset from shared space or user storage."""

    dataset_path: str = Field(
        description=(
            "Path of the dataset to preview. Can be a shared dataset name "
            "(e.g. 'openai/gsm8k'), a shared dataset with file path "
            "(e.g. 'openai/gsm8k/data/train.jsonl'), or a user storage "
            "relative path (e.g. 'datasets/my_data/' or "
            "'datasets/my_data/train.jsonl')."
        ),
    )
    n: int = Field(
        default=10,
        description=(
            "Maximum number of sample rows or text lines to return (1-100). "
            "Defaults to 10; large truncated files may return fewer."
        ),
        ge=1,
        le=_MAX_REQUESTED_SAMPLES,
        validation_alias=AliasChoices("n", "max_samples"),
    )
    mode: Literal["inspect", "sample"] = Field(
        default="inspect",
        description=(
            "Use 'inspect' for the existing bounded preview behavior. Use "
            "'sample' to materialize up to n selected user-storage files or "
            "folders in the conversation workspace and create a JSONL manifest."
        ),
    )
    sample_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Optional exact user-storage file or folder paths to materialize in "
            "sample mode. When omitted, up to n entries are selected from "
            "dataset_path in stable path order."
        ),
    )
    vision_ocr: bool = Field(
        default=True,
        description=(
            "For storage images, call the configured DF vision model for OCR and "
            "a short visual summary in both inspect and sample modes."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Preview dataset: ", style="bold blue")
        content.append(self.dataset_path)
        return content


class PreviewDatasetObservation(Observation):
    """Statistics and sample rows of a storage dataset."""

    dataset_path: str = Field(description="The storage path that was previewed.")
    files: list[str] = Field(
        default_factory=list,
        description="Data files found under the path (relative to it).",
    )
    num_rows: int | None = Field(
        default=None, description="Total row count across data files."
    )
    columns: list[str] = Field(
        default_factory=list, description="Top-level fields of each row."
    )
    p95_sequence_length: int | None = Field(
        default=None,
        description="P95 of per-row token length (prompt + response).",
    )
    has_vision: bool | None = Field(
        default=None, description="Whether rows contain image/video fields."
    )
    sample_rows: list[dict[str, Any]] = Field(
        default_factory=list, description="A few raw sample rows."
    )
    requested_rows: int | None = Field(
        default=None,
        description="Maximum sample rows requested by the caller.",
    )
    object_name: str | None = Field(
        default=None, description="Storage object name returned by the metadata API."
    )
    bucket_name: str | None = Field(
        default=None, description="Storage bucket returned by the metadata API."
    )
    size: int | None = Field(default=None, description="Object size in bytes.")
    content_type: str | None = Field(
        default=None, description="Object MIME type returned by storage."
    )
    is_dir: bool | None = Field(
        default=None, description="Whether the previewed storage path is a directory."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Object metadata returned by storage."
    )
    preview_file_path: str | None = Field(
        default=None,
        description=(
            "Concrete file path used for content sampling when dataset_path is a "
            "directory."
        ),
    )
    previewed_rows: int | None = Field(
        default=None,
        description=(
            "Rows or lines inspected in the bounded preview. For truncated large "
            "files this is not the total row count."
        ),
    )
    preview_truncated: bool = Field(
        default=False,
        description="Whether content sampling stopped at the preview byte limit.",
    )
    preview_error: str | None = Field(
        default=None,
        description="Non-fatal content parsing issue, when metadata lookup succeeded.",
    )
    error_code: str | None = Field(
        default=None,
        description="Machine-readable input or preview error code.",
    )
    source: str | None = Field(
        default=None,
        description=(
            "Data source: 'shared' for shared dataset space, "
            "'storage' for user storage."
        ),
    )
    entries: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Files and folders found directly under a storage directory.",
    )
    directory_summary: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Machine-readable structural summary for storage directories. "
            "This is a decision aid only; it does not define a dataset schema."
        ),
    )
    local_sample_paths: list[str] = Field(
        default_factory=list,
        description="Workspace-relative paths materialized for local sample runs.",
    )
    sample_manifest_path: str | None = Field(
        default=None,
        description="Workspace-relative JSONL manifest created in sample mode.",
    )
    vision_previews: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Gemma OCR and visual summaries for sampled images.",
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        """Keep raw images out of the main coding model's context."""

        text_content = [item for item in self.content if isinstance(item, TextContent)]
        if self.is_error:
            return [
                TextContent(text=self.ERROR_MESSAGE_HEADER),
                *text_content,
            ]
        return text_content

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Dataset preview: ", style="bold green")
        content.append(self.dataset_path)
        if self.num_rows is not None:
            content.append(f"\nrows={self.num_rows}")
        if self.columns:
            content.append(f"\ncolumns={', '.join(self.columns)}")
        return content


_PREVIEW_DATASET_DESCRIPTION = """Preview a dataset from shared space or user storage.

This tool inspects dataset content from two sources (tried in order):
1. Shared dataset space: platform-curated datasets identified by org/name
   (e.g. 'openai/gsm8k', 'pyromind/self-cognition'). Supports listing files
   and previewing content directly.
2. User storage: files/directories the user uploaded, identified by a
   storage-relative path (e.g. 'datasets/my_data/train.jsonl' or
   'datasets/my_data/').

The tool automatically determines the source:
- If the path matches a known shared dataset (exact or prefix), it uses the
  shared space API.
- Otherwise, it falls back to user storage.

When only a dataset/folder name is given (no specific file):
- Shared space: lists available files and auto-selects the first previewable
  one. The observation includes the full file list so you can ask the user
  which file to preview, or call this tool again with a specific file path
  like 'openai/gsm8k/data/train.jsonl'.
- User storage: if the folder contains multiple files, the tool returns the
  file list (with sizes and modified times) WITHOUT previewing anything.
  You MUST ask the user which file to preview, then call this tool again
  with the exact file path. If the folder has exactly one file, it is
  previewed directly.

Use mode='sample' for user storage after inspection. It materializes at most
three selected files or folders inside the conversation workspace, preserves
their storage-relative layout, and returns a sample_manifest_path for
df_run_pipeline. Image samples are sent to the configured DF vision model
(normally Gemma) for OCR and a short visual summary.

Returns:
- files found under the path
- row count (exact for small files, estimated for large)
- column names and sample rows for JSONL, CSV/TSV, and text formats
- source indicator ('shared' or 'storage')

For user storage: files up to 10KB are downloaded in full; larger files use one
head byte range plus random ranges. When preview_truncated=true, sample lines may
be partial byte fragments, num_rows is unset, and samples are format hints only.
"""


class PreviewDatasetExecutor(
    ToolExecutor[PreviewDatasetAction, PreviewDatasetObservation]
):
    """Preview datasets from shared space or user storage."""

    def __init__(
        self,
        storage_base_url: str | None = None,
        headers: dict[str, str] | None = None,
        secret_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        max_preview_bytes: int = _DEFAULT_PREVIEW_BYTES,
    ) -> None:
        base_url = storage_base_url or _default_storage_base_url()
        self._storage_base_url = base_url.rstrip("/")
        self._shared_base_url = self._storage_base_url.replace(
            "/storage_api", "/api/v1"
        )
        self._headers = dict(headers or {})
        self._secret_headers = dict(secret_headers or {})
        self._timeout = timeout
        self._max_preview_bytes = min(max_preview_bytes, _MAX_PREVIEW_BYTES)

    def __call__(
        self,
        action: PreviewDatasetAction,
        conversation: BaseConversation | None = None,
    ) -> PreviewDatasetObservation:
        dataset_path = action.dataset_path.strip()
        if not dataset_path:
            return PreviewDatasetObservation.from_text(
                text="dataset_path must be a non-empty path.",
                is_error=True,
                dataset_path=action.dataset_path,
            )

        try:
            headers = self._resolve_headers(conversation, json_content=True)
        except ValueError as exc:
            return PreviewDatasetObservation.from_text(
                text=str(exc),
                is_error=True,
                dataset_path=dataset_path,
            )

        if action.mode == "sample":
            return self._storage_sample(action, dataset_path, headers, conversation)

        # Option C: try shared dataset space first
        shared_result = self._try_shared_preview(dataset_path, action.n, headers)
        if shared_result is not None:
            return shared_result

        # Fall back to user storage
        return self._storage_preview(
            dataset_path,
            action.n,
            headers,
            vision_ocr=action.vision_ocr,
        )

    # ------------------------------------------------------------------
    # Shared dataset space
    # ------------------------------------------------------------------

    def _try_shared_preview(
        self,
        dataset_path: str,
        n: int,
        headers: dict[str, str],
    ) -> PreviewDatasetObservation | None:
        """Attempt shared space preview. Returns None if not a shared dataset."""
        datasets = self._shared_list_datasets(headers)
        if datasets is None:
            return None

        match = _match_shared_dataset(dataset_path, datasets)
        if match is None:
            return None

        dataset_name, file_path = match

        if not file_path:
            return self._shared_resolve_and_preview(dataset_name, n, headers)

        return self._shared_preview_file(dataset_name, file_path, n, headers)

    def _shared_resolve_and_preview(
        self,
        dataset_name: str,
        n: int,
        headers: dict[str, str],
    ) -> PreviewDatasetObservation:
        """List files in a shared dataset and preview the first previewable one."""
        files_result = self._shared_list_files(dataset_name, headers)
        if isinstance(files_result, PreviewDatasetObservation):
            return files_result

        file_paths = [f["path"] for f in files_result]
        preview_file = _select_preview_file(file_paths)

        if not preview_file:
            file_list_text = "\n".join(
                f"  - {f['path']} ({f.get('human_size', '?')})" for f in files_result
            )
            return PreviewDatasetObservation.from_text(
                text=(
                    f"Shared dataset '{dataset_name}' contains no previewable "
                    f"text files.\nAvailable files:\n{file_list_text}"
                ),
                dataset_path=dataset_name,
                files=file_paths,
                is_dir=True,
                source="shared",
            )

        if len(files_result) > 1:
            file_list_text = "\n".join(
                f"  - {f['path']} ({f.get('human_size', '?')})" for f in files_result
            )
            preview_result = self._shared_preview_file(
                dataset_name, preview_file, n, headers
            )
            if not preview_result.is_error:
                header = (
                    f"Dataset '{dataset_name}' has {len(files_result)} "
                    f"files. Auto-selected '{preview_file}' for preview.\n"
                    f"Available files:\n{file_list_text}\n\n"
                )
                new_content = [
                    TextContent(text=header + item.text)
                    if isinstance(item, TextContent)
                    else item
                    for item in preview_result.content
                ]
                preview_result = preview_result.model_copy(
                    update={"files": file_paths, "content": new_content}
                )
            return preview_result

        return self._shared_preview_file(dataset_name, preview_file, n, headers)

    def _shared_list_datasets(
        self,
        headers: dict[str, str],
    ) -> list[str] | None:
        """Fetch shared dataset list. Returns None on failure (allows fallback)."""
        try:
            response = httpx.get(
                f"{self._shared_base_url}/datasets",
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.RequestError:
            return None
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        datasets = data.get("datasets")
        if not isinstance(datasets, list):
            return None
        return [str(d) for d in datasets]

    def _shared_list_files(
        self,
        dataset_name: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]] | PreviewDatasetObservation:
        """List files in a shared dataset."""
        try:
            response = httpx.get(
                f"{self._shared_base_url}/datasets/files",
                headers=headers,
                params={"dataset": dataset_name},
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            return PreviewDatasetObservation.from_text(
                text=(
                    f"Failed to list shared dataset files: {type(exc).__name__}: {exc}"
                ),
                is_error=True,
                dataset_path=dataset_name,
                source="shared",
            )
        if response.status_code >= 400:
            return PreviewDatasetObservation.from_text(
                text=(
                    f"Shared dataset files API returned HTTP "
                    f"{response.status_code}: {_truncate_text(response.text)}"
                ),
                is_error=True,
                dataset_path=dataset_name,
                source="shared",
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return PreviewDatasetObservation.from_text(
                text="Shared dataset files API returned invalid JSON.",
                is_error=True,
                dataset_path=dataset_name,
                source="shared",
            )
        if not isinstance(payload, dict) or payload.get("success") is not True:
            message = "Shared dataset files API failed."
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    message = str(err.get("message") or message)
            return PreviewDatasetObservation.from_text(
                text=message,
                is_error=True,
                dataset_path=dataset_name,
                source="shared",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            return PreviewDatasetObservation.from_text(
                text="Shared dataset files API response missing data.",
                is_error=True,
                dataset_path=dataset_name,
                source="shared",
            )
        files = data.get("files")
        if not isinstance(files, list):
            return PreviewDatasetObservation.from_text(
                text="Shared dataset files API response missing files list.",
                is_error=True,
                dataset_path=dataset_name,
                source="shared",
            )
        return [f for f in files if isinstance(f, dict)]

    def _shared_preview_file(
        self,
        dataset_name: str,
        file_path: str,
        n: int,
        headers: dict[str, str],
    ) -> PreviewDatasetObservation:
        """Preview a specific file in a shared dataset."""
        try:
            response = httpx.get(
                f"{self._shared_base_url}/datasets/preview",
                headers=headers,
                params={
                    "dataset": dataset_name,
                    "file_path": file_path,
                    "lines": n,
                },
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            return PreviewDatasetObservation.from_text(
                text=(
                    f"Failed to preview shared dataset file: "
                    f"{type(exc).__name__}: {exc}"
                ),
                is_error=True,
                dataset_path=f"{dataset_name}/{file_path}",
                source="shared",
            )
        if response.status_code >= 400:
            return PreviewDatasetObservation.from_text(
                text=(
                    f"Shared dataset preview API returned HTTP "
                    f"{response.status_code}: {_truncate_text(response.text)}"
                ),
                is_error=True,
                dataset_path=f"{dataset_name}/{file_path}",
                source="shared",
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return PreviewDatasetObservation.from_text(
                text="Shared dataset preview API returned invalid JSON.",
                is_error=True,
                dataset_path=f"{dataset_name}/{file_path}",
                source="shared",
            )
        if not isinstance(payload, dict) or payload.get("success") is not True:
            message = "Shared dataset preview API failed."
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    message = str(err.get("message") or message)
            return PreviewDatasetObservation.from_text(
                text=message,
                is_error=True,
                dataset_path=f"{dataset_name}/{file_path}",
                source="shared",
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            return PreviewDatasetObservation.from_text(
                text="Shared dataset preview API response missing data.",
                is_error=True,
                dataset_path=f"{dataset_name}/{file_path}",
                source="shared",
            )

        return self._build_shared_observation(dataset_name, file_path, data, n)

    def _build_shared_observation(
        self,
        dataset_name: str,
        file_path: str,
        data: dict[str, Any],
        requested_rows: int,
    ) -> PreviewDatasetObservation:
        """Build observation from shared preview API response data."""
        preview = data.get("preview")
        if not isinstance(preview, dict):
            return PreviewDatasetObservation.from_text(
                text=(
                    f"Shared dataset '{dataset_name}/{file_path}' preview "
                    "not available (binary or unsupported format)."
                ),
                dataset_path=f"{dataset_name}/{file_path}",
                preview_file_path=file_path,
                source="shared",
                size=_optional_int(data.get("file_size")),
            )

        preview_type = str(preview.get("type") or "text")
        if preview_type not in {"text", "parquet"}:
            message = str(
                preview.get("message") or "Preview not supported for this file type."
            )
            return PreviewDatasetObservation.from_text(
                text=f"Shared dataset '{dataset_name}/{file_path}': {message}",
                dataset_path=f"{dataset_name}/{file_path}",
                preview_file_path=file_path,
                source="shared",
                size=_optional_int(data.get("file_size")),
            )

        lines: list[str] = []
        if preview_type == "text":
            raw_lines = preview.get("lines")
            if isinstance(raw_lines, list):
                lines = [str(line) for line in raw_lines]
        elif preview_type == "parquet":
            rows = preview.get("rows")
            if isinstance(rows, list):
                lines = [
                    json.dumps(row, ensure_ascii=False)
                    for row in rows
                    if isinstance(row, dict)
                ]

        total_lines = preview.get("total_lines") or preview.get("total_rows")
        truncated = data.get("truncated", False)
        if isinstance(total_lines, int) and total_lines < 0:
            total_lines = None

        parsed = _parse_preview_chunks(
            file_path,
            "",
            [
                _PreviewChunk(
                    content="\n".join(lines).encode("utf-8"),
                    starts_at_zero=True,
                    ends_at_eof=not truncated,
                )
            ],
            max_samples=requested_rows,
            truncated=truncated,
        )
        parsed["sample_rows"] = _trim_sample_rows(
            parsed["sample_rows"], self._max_preview_bytes
        )
        parsed["columns"] = _collect_columns(parsed["sample_rows"])

        if not truncated and isinstance(total_lines, int) and total_lines > 0:
            parsed["num_rows"] = total_lines

        text = _format_shared_preview_text(
            dataset_name=dataset_name,
            file_path=file_path,
            data=data,
            parsed=parsed,
            requested_rows=requested_rows,
            truncated=truncated,
        )

        return PreviewDatasetObservation.from_text(
            text=text,
            dataset_path=f"{dataset_name}/{file_path}",
            files=[file_path],
            num_rows=parsed["num_rows"],
            columns=parsed["columns"],
            has_vision=_infer_has_vision(parsed["columns"], parsed["sample_rows"]),
            sample_rows=parsed["sample_rows"],
            requested_rows=requested_rows,
            preview_file_path=file_path,
            previewed_rows=parsed["previewed_rows"],
            preview_truncated=truncated,
            preview_error=parsed["preview_error"],
            size=_optional_int(data.get("file_size")),
            source="shared",
        )

    # ------------------------------------------------------------------
    # User storage (original flow)
    # ------------------------------------------------------------------

    def _storage_sample(
        self,
        action: PreviewDatasetAction,
        dataset_path: str,
        headers: dict[str, str],
        conversation: BaseConversation | None,
    ) -> PreviewDatasetObservation:
        try:
            selected_paths, entries = self._select_storage_samples(
                dataset_path,
                action.sample_paths,
                min(action.n, _DEFAULT_SAMPLE_COUNT),
                headers,
            )
            workspace_dir = _resolve_workspace_dir(conversation)
            preview_key = hashlib.sha256(
                json.dumps(
                    {
                        "dataset_path": dataset_path,
                        "sample_paths": selected_paths,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:16]
            preview_root = (
                workspace_dir
                / "public_data"
                / "data-preparation"
                / "previews"
                / preview_key
            )
            preview_root.mkdir(parents=True, exist_ok=True)

            manifest_rows: list[dict[str, Any]] = []
            local_paths: list[str] = []
            vision_previews: list[dict[str, Any]] = []
            preview_images: list[ImageContent] = []
            total_bytes = 0
            total_files = 0
            vision_images = 0

            for index, selected_path in enumerate(selected_paths):
                storage_files = self._storage_sample_files(selected_path, headers)
                row_files: list[str] = []
                row_images: list[str] = []
                selected_workspace_path: str | None = None
                selected_manifest_path: str | None = None

                for storage_file in storage_files:
                    total_files += 1
                    if total_files > _MAX_SAMPLE_FILES:
                        raise ValueError(
                            "Sample selection exceeds the "
                            f"{_MAX_SAMPLE_FILES}-file limit."
                        )
                    content = download_file_from_pyromind(
                        storage_path=storage_file.path,
                        storage_base_url=self._storage_base_url,
                        headers=headers,
                        timeout=self._timeout,
                        max_bytes=_MAX_SAMPLE_FILE_BYTES,
                    )
                    total_bytes += len(content)
                    if total_bytes > _MAX_SAMPLE_TOTAL_BYTES:
                        raise ValueError(
                            "Sample selection exceeds the "
                            f"{_human_size(_MAX_SAMPLE_TOTAL_BYTES)} total limit."
                        )

                    local_file = _sample_local_file(preview_root, storage_file.path)
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    local_file.write_bytes(content)
                    workspace_path = local_file.relative_to(workspace_dir).as_posix()
                    manifest_file_path = local_file.relative_to(preview_root).as_posix()
                    row_files.append(manifest_file_path)
                    if selected_workspace_path is None:
                        selected_workspace_path = workspace_path
                        selected_manifest_path = manifest_file_path

                    if _is_vision_path(storage_file.path):
                        row_images.append(manifest_file_path)
                        if (
                            _is_image_path(storage_file.path)
                            and len(preview_images) < _DEFAULT_SAMPLE_COUNT
                            and len(content) <= _MAX_INLINE_IMAGE_BYTES
                        ):
                            preview_images.append(
                                ImageContent(
                                    image_urls=[
                                        "data:"
                                        f"{_vision_content_type(storage_file.path)}"
                                        ";base64,"
                                        + base64.b64encode(content).decode("ascii")
                                    ]
                                )
                            )
                        if action.vision_ocr and (
                            vision_images < _MAX_VISION_PREVIEW_IMAGES
                        ):
                            vision_images += 1
                            if _is_image_path(storage_file.path):
                                vision_previews.append(
                                    self._preview_image_with_vision_model(
                                        source_path=storage_file.path,
                                        local_path=workspace_path,
                                        content=content,
                                    )
                                )

                selected_local_path = _sample_local_file(preview_root, selected_path)
                if selected_local_path.exists():
                    selected_workspace_path = selected_local_path.relative_to(
                        workspace_dir
                    ).as_posix()
                    selected_manifest_path = selected_local_path.relative_to(
                        preview_root
                    ).as_posix()
                if selected_workspace_path is None or selected_manifest_path is None:
                    raise ValueError(
                        f"Sample path contains no downloadable files: {selected_path}"
                    )
                local_paths.append(selected_workspace_path)
                manifest_row: dict[str, Any] = {
                    "id": f"sample-{index}",
                    "source_path": selected_path,
                    "local_path": selected_manifest_path,
                    "files": row_files,
                    "images": row_images,
                }
                if row_images:
                    manifest_row["image_path"] = row_images[0]
                manifest_rows.append(manifest_row)

            manifest_path = preview_root / "sample_manifest.jsonl"
            with manifest_path.open("w", encoding="utf-8") as manifest_file:
                for row in manifest_rows:
                    manifest_file.write(
                        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    )

            manifest_relative = manifest_path.relative_to(workspace_dir).as_posix()
            summary_text = (
                f"Materialized {len(manifest_rows)} sample unit(s), "
                f"{total_files} file(s), {_human_size(total_bytes)}. "
                f"Manifest: {manifest_relative}"
            )
            for preview in vision_previews:
                summary_text += (
                    f"\n\n--- vision preview: {preview['source_path']} ---\n"
                    f"{preview['visual_summary'] or '; '.join(preview['warnings'])}"
                )
            return PreviewDatasetObservation(
                content=[TextContent(text=summary_text), *preview_images],
                dataset_path=dataset_path,
                files=[
                    file_path
                    for row in manifest_rows
                    for file_path in cast(list[str], row["files"])
                ],
                entries=entries,
                local_sample_paths=local_paths,
                sample_manifest_path=manifest_relative,
                vision_previews=vision_previews,
                requested_rows=action.n,
                previewed_rows=len(manifest_rows),
                has_vision=any(row["images"] for row in manifest_rows),
                source="storage",
            )
        except (OSError, ValueError) as exc:
            return PreviewDatasetObservation.from_text(
                text=f"Failed to materialize storage samples: {exc}",
                is_error=True,
                dataset_path=dataset_path,
                source="storage",
            )

    def _select_storage_samples(
        self,
        dataset_path: str,
        requested_paths: list[str],
        limit: int,
        headers: dict[str, str],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if requested_paths:
            selected = [
                _normalize_storage_sample_path(path) for path in requested_paths
            ]
            if len(selected) > limit:
                raise ValueError(
                    f"sample_paths contains {len(selected)} entries; at most "
                    f"{limit} are allowed."
                )
            return selected, []

        normalized_path = _normalize_storage_sample_path(dataset_path)
        is_directory = _looks_like_directory(dataset_path)
        if not is_directory:
            metadata_result = self._get_metadata(normalized_path, headers)
            if isinstance(metadata_result, PreviewDatasetObservation):
                raise ValueError(metadata_result.text)
            is_directory = metadata_result.get("is_dir") is True
        if not is_directory:
            return [normalized_path], []

        list_result = self._list_entries(normalized_path, headers)
        if isinstance(list_result, PreviewDatasetObservation):
            raise ValueError(list_result.text)
        sorted_entries = sorted(list_result, key=lambda item: item.path)
        visible_entries = [
            entry
            for entry in sorted_entries
            if not PurePosixPath(entry.path).name.startswith(".")
        ]
        if visible_entries:
            sorted_entries = visible_entries
        selected_entries = sorted_entries[:limit]
        if not selected_entries:
            raise ValueError(f"No files or folders found under {dataset_path}.")
        return (
            [entry.path for entry in selected_entries],
            [_storage_entry_dict(entry) for entry in sorted_entries],
        )

    def _storage_sample_files(
        self,
        selected_path: str,
        headers: dict[str, str],
    ) -> list[_StorageFileInfo]:
        normalized_path = _normalize_storage_sample_path(selected_path)
        if _looks_like_directory(selected_path):
            return self._walk_storage_files(normalized_path, headers)

        metadata_result = self._get_metadata(normalized_path, headers)
        if isinstance(metadata_result, PreviewDatasetObservation):
            # Virtual folders can be listable even when get_file_metadata says
            # they do not exist.
            try:
                return self._walk_storage_files(normalized_path, headers)
            except ValueError as exc:
                raise ValueError(metadata_result.text) from exc
        if metadata_result.get("is_dir") is True:
            return self._walk_storage_files(normalized_path, headers)
        return [
            _StorageFileInfo(
                path=normalized_path,
                name=PurePosixPath(normalized_path).name,
                size=_optional_int(metadata_result.get("size")),
                last_modified=None,
            )
        ]

    def _walk_storage_files(
        self,
        root_path: str,
        headers: dict[str, str],
    ) -> list[_StorageFileInfo]:
        pending: list[tuple[str, int]] = [(root_path, 0)]
        files: list[_StorageFileInfo] = []
        while pending:
            current_path, depth = pending.pop(0)
            if depth > _MAX_SAMPLE_DIRECTORY_DEPTH:
                raise ValueError(
                    "Sample folder nesting exceeds the "
                    f"{_MAX_SAMPLE_DIRECTORY_DEPTH}-level limit."
                )
            list_result = self._list_entries(current_path, headers)
            if isinstance(list_result, PreviewDatasetObservation):
                raise ValueError(list_result.text)
            for entry in sorted(list_result, key=lambda item: item.path):
                if entry.is_dir:
                    pending.append((entry.path, depth + 1))
                else:
                    files.append(entry)
                    if len(files) > _MAX_SAMPLE_FILES:
                        raise ValueError(
                            f"Sample folder exceeds the {_MAX_SAMPLE_FILES}-file limit."
                        )
        return files

    def _preview_image_with_vision_model(
        self,
        *,
        source_path: str,
        local_path: str,
        content: bytes,
    ) -> dict[str, Any]:
        try:
            summary = _call_vision_preview_model(
                source_path=source_path,
                content=content,
                timeout=self._timeout,
            )
            return {
                "source_path": source_path,
                "local_path": local_path,
                "ocr_text": summary,
                "visual_summary": summary,
                "warnings": [],
            }
        except ValueError as exc:
            return {
                "source_path": source_path,
                "local_path": local_path,
                "ocr_text": "",
                "visual_summary": "",
                "warnings": [str(exc)],
            }

    def _storage_preview(
        self,
        dataset_path: str,
        n: int,
        headers: dict[str, str],
        *,
        vision_ocr: bool,
    ) -> PreviewDatasetObservation:
        files: list[str] = []
        preview_path = dataset_path
        metadata: dict[str, Any] | None = None

        if _looks_like_directory(dataset_path):
            dir_result = self._resolve_storage_directory(dataset_path, n, headers)
            if isinstance(dir_result, PreviewDatasetObservation):
                return dir_result
            files, preview_path = dir_result

        metadata_result = self._get_metadata(preview_path, headers)
        if isinstance(metadata_result, PreviewDatasetObservation):
            # Storage backends may not expose metadata for virtual folders.
            # Fall back to file_list so callers need not add a trailing slash.
            dir_result = self._resolve_storage_directory(dataset_path, n, headers)
            if isinstance(dir_result, PreviewDatasetObservation):
                return dir_result
            files, preview_path = dir_result
            metadata_result = self._get_metadata(preview_path, headers)
            if isinstance(metadata_result, PreviewDatasetObservation):
                return metadata_result
        metadata = metadata_result

        if metadata.get("is_dir") is True:
            dir_result = self._resolve_storage_directory(dataset_path, n, headers)
            if isinstance(dir_result, PreviewDatasetObservation):
                return dir_result
            files, preview_path = dir_result
            metadata_result = self._get_metadata(preview_path, headers)
            if isinstance(metadata_result, PreviewDatasetObservation):
                return metadata_result
            metadata = metadata_result

        if not files:
            files = [preview_path]

        size = _optional_int(metadata.get("size"))
        content_type = str(metadata.get("content_type") or "")
        if _is_image_path(preview_path) or content_type.lower().startswith("image/"):
            return self._storage_image_preview(
                dataset_path=dataset_path,
                preview_path=preview_path,
                files=files,
                metadata=metadata,
                headers=headers,
                vision_ocr=vision_ocr,
            )

        if size == 0:
            preview_chunks, preview_truncated = [], False
        else:
            preview_url_result = self._get_download_url(preview_path, headers)
            if isinstance(preview_url_result, PreviewDatasetObservation):
                return preview_url_result
            content_result = self._download_preview(
                preview_url_result,
                preview_path,
                size,
                str(metadata.get("content_type") or ""),
            )
            if isinstance(content_result, PreviewDatasetObservation):
                return content_result
            preview_chunks, preview_truncated = content_result

        parsed = _parse_preview_chunks(
            preview_path,
            str(metadata.get("content_type") or ""),
            preview_chunks,
            max_samples=n,
            truncated=preview_truncated,
        )
        parsed["sample_rows"] = _trim_sample_rows(
            parsed["sample_rows"], self._max_preview_bytes
        )
        parsed["columns"] = _collect_columns(parsed["sample_rows"])
        text = _format_preview_text(
            dataset_path=dataset_path,
            preview_path=preview_path,
            files=files,
            metadata=metadata,
            parsed=parsed,
            requested_rows=n,
            preview_truncated=preview_truncated,
        )

        return PreviewDatasetObservation.from_text(
            text=text,
            dataset_path=dataset_path,
            files=files,
            num_rows=parsed["num_rows"],
            columns=parsed["columns"],
            p95_sequence_length=None,
            has_vision=_infer_has_vision(parsed["columns"], parsed["sample_rows"]),
            sample_rows=parsed["sample_rows"],
            requested_rows=n,
            preview_file_path=preview_path,
            previewed_rows=parsed["previewed_rows"],
            preview_truncated=preview_truncated,
            preview_error=parsed["preview_error"],
            source="storage",
            **_metadata_observation_fields(metadata),
        )

    def _storage_image_preview(
        self,
        *,
        dataset_path: str,
        preview_path: str,
        files: list[str],
        metadata: dict[str, Any],
        headers: dict[str, str],
        vision_ocr: bool,
    ) -> PreviewDatasetObservation:
        try:
            content = download_file_from_pyromind(
                storage_path=preview_path,
                storage_base_url=self._storage_base_url,
                headers=headers,
                timeout=self._timeout,
                max_bytes=_MAX_SAMPLE_FILE_BYTES,
            )
        except ValueError as exc:
            return PreviewDatasetObservation.from_text(
                text=f"Failed to preview storage image: {exc}",
                is_error=True,
                dataset_path=dataset_path,
                source="storage",
            )

        vision_previews: list[dict[str, Any]] = []
        if vision_ocr:
            vision_previews.append(
                self._preview_image_with_vision_model(
                    source_path=preview_path,
                    local_path="",
                    content=content,
                )
            )
            preview = vision_previews[0]
            summary = preview["visual_summary"] or "; ".join(preview["warnings"])
        else:
            summary = "Gemma OCR and visual summary disabled for this request."

        image_content: list[ImageContent] = []
        if len(content) <= _MAX_INLINE_IMAGE_BYTES:
            image_content.append(
                ImageContent(
                    image_urls=[
                        "data:"
                        f"{_vision_content_type(preview_path)}"
                        ";base64," + base64.b64encode(content).decode("ascii")
                    ]
                )
            )

        text = (
            f"Image preview: {preview_path}\n"
            f"size={len(content)} bytes\n"
            f"vision_summary={summary}"
        )
        return PreviewDatasetObservation(
            content=[TextContent(text=text), *image_content],
            dataset_path=dataset_path,
            files=files,
            has_vision=True,
            preview_file_path=preview_path,
            previewed_rows=1,
            vision_previews=vision_previews,
            source="storage",
            **_metadata_observation_fields(metadata),
        )

    def _resolve_storage_directory(
        self,
        dataset_path: str,
        n: int,  # noqa: ARG002
        headers: dict[str, str],
    ) -> tuple[list[str], str] | PreviewDatasetObservation:
        """Resolve a storage directory into a concrete preview file.

        When the directory contains multiple files, returns an observation
        listing file details so the agent asks the user which file to
        preview. When exactly one file exists, auto-selects it.
        """
        list_result = self._list_entries(dataset_path, headers)
        if isinstance(list_result, PreviewDatasetObservation):
            return list_result

        entries = list_result
        file_infos = [entry for entry in entries if not entry.is_dir]
        file_paths = [f.path for f in file_infos]

        if not entries:
            return PreviewDatasetObservation.from_text(
                text=f"No files or folders found under {dataset_path}.",
                dataset_path=dataset_path,
                files=file_paths,
                is_dir=True,
                source="storage",
                directory_summary=_empty_directory_summary(),
            )

        if len(entries) == 1 and len(file_paths) == 1:
            return file_paths, file_paths[0]

        file_list_text = "\n".join(
            f"  - {entry.path}"
            f" ({'folder' if entry.is_dir else _human_size(entry.size)}"
            + (f", modified {entry.last_modified}" if entry.last_modified else "")
            + ")"
            for entry in entries
        )
        if len(file_infos) == len(entries):
            summary = (
                f"Directory '{dataset_path}' contains {len(file_paths)} files. "
                "Ask the user which file to preview, then call this tool again "
                "with the exact file path, or use mode='sample' to materialize "
                "selected files."
            )
            list_label = "Available files"
        else:
            summary = (
                f"Directory '{dataset_path}' contains {len(entries)} entries. "
                "Call this tool again with an exact file path to preview content, "
                "or use mode='sample' with selected sample_paths to materialize "
                "files or folders."
            )
            list_label = "Available entries"
        directory_summary = self._build_directory_summary(entries, headers)
        directory_summary.update(_infer_directory_layout(entries, directory_summary))
        summary_text = (
            f"{summary}\n{list_label}:\n{file_list_text}\n\n"
            f"Directory summary: {directory_summary['layout_evidence']}"
        )
        return PreviewDatasetObservation.from_text(
            text=summary_text,
            dataset_path=dataset_path,
            files=file_paths,
            entries=[_storage_entry_dict(entry) for entry in entries],
            is_dir=True,
            source="storage",
            directory_summary=directory_summary,
        )

    def _build_directory_summary(
        self,
        entries: list[_StorageFileInfo],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        files = [entry for entry in entries if not entry.is_dir]
        folders = [entry for entry in entries if entry.is_dir]
        top_suffix_counts = _suffix_counts(files)
        sampled_child_folders: list[dict[str, Any]] = []
        child_listing_errors: list[dict[str, str]] = []

        for folder in folders[:_MAX_DIRECTORY_CHILD_SAMPLES]:
            child_result = self._list_entries(folder.path, headers)
            if isinstance(child_result, PreviewDatasetObservation):
                child_listing_errors.append(
                    {"path": folder.path, "error": child_result.text}
                )
                continue
            child_files = [entry for entry in child_result if not entry.is_dir]
            child_folders = [entry for entry in child_result if entry.is_dir]
            sampled_child_folders.append(
                {
                    "path": folder.path,
                    "file_count": len(child_files),
                    "folder_count": len(child_folders),
                    "file_names": sorted(entry.name for entry in child_files),
                    "suffix_counts": _suffix_counts(child_files),
                }
            )

        return {
            "top_level_folder_count": len(folders),
            "top_level_file_count": len(files),
            "top_level_suffix_counts": top_suffix_counts,
            "top_level_type_counts": _type_counts(top_suffix_counts),
            "sampled_child_folders": sampled_child_folders,
            "child_listing_errors": child_listing_errors,
            "repeated_file_name_set": _shared_child_value(
                sampled_child_folders, "file_names"
            ),
            "repeated_suffix_set": _shared_child_value(
                sampled_child_folders, "suffix_counts"
            ),
        }

    def _get_metadata(
        self,
        path: str,
        headers: dict[str, str],
    ) -> dict[str, Any] | PreviewDatasetObservation:
        payload_result = self._post_json("get_file_metadata", {"path": path}, headers)
        if isinstance(payload_result, str):
            return PreviewDatasetObservation.from_text(
                text=payload_result,
                is_error=True,
                dataset_path=path,
            )
        data_result = _extract_api_data("get_file_metadata", payload_result)
        if isinstance(data_result, str):
            return PreviewDatasetObservation.from_text(
                text=data_result,
                is_error=True,
                dataset_path=path,
            )
        return data_result

    def _list_files(
        self,
        path: str,
        headers: dict[str, str],
    ) -> list[_StorageFileInfo] | PreviewDatasetObservation:
        entries_result = self._list_entries(path, headers)
        if isinstance(entries_result, PreviewDatasetObservation):
            return entries_result
        return [entry for entry in entries_result if not entry.is_dir]

    def _list_entries(
        self,
        path: str,
        headers: dict[str, str],
    ) -> list[_StorageFileInfo] | PreviewDatasetObservation:
        payload_result = self._post_json(
            "file_list", {"path": path, "search": ""}, headers
        )
        if isinstance(payload_result, str):
            return PreviewDatasetObservation.from_text(
                text=payload_result,
                is_error=True,
                dataset_path=path,
            )
        data_result = _extract_api_data("file_list", payload_result)
        if isinstance(data_result, str):
            return PreviewDatasetObservation.from_text(
                text=data_result,
                is_error=True,
                dataset_path=path,
            )

        raw_files = data_result.get("list")
        if not isinstance(raw_files, list):
            return PreviewDatasetObservation.from_text(
                text="Pyromind storage file_list API response is missing list data.",
                is_error=True,
                dataset_path=path,
            )

        entries: list[_StorageFileInfo] = []
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            entry_type = str(item.get("type") or "").lower()
            if entry_type not in {"file", "folder", "directory", "dir"}:
                continue
            item_path = item.get("path")
            if item_path is not None:
                entries.append(
                    _StorageFileInfo(
                        path=str(item_path),
                        name=str(item.get("name") or ""),
                        size=_optional_int(item.get("size")),
                        last_modified=_optional_str(item.get("last_modified")),
                        is_dir=entry_type != "file",
                    )
                )
        return entries

    def _get_download_url(
        self,
        path: str,
        headers: dict[str, str],
    ) -> str | PreviewDatasetObservation:
        payload_result = self._post_json("get_url", {"path": path}, headers)
        if isinstance(payload_result, str):
            return PreviewDatasetObservation.from_text(
                text=payload_result,
                is_error=True,
                dataset_path=path,
            )
        data_result = _extract_api_data("get_url", payload_result)
        if isinstance(data_result, str):
            return PreviewDatasetObservation.from_text(
                text=data_result,
                is_error=True,
                dataset_path=path,
            )

        url = data_result.get("url")
        if not isinstance(url, str) or not url.strip():
            return PreviewDatasetObservation.from_text(
                text="Pyromind storage get_url API response is missing url data.",
                is_error=True,
                dataset_path=path,
            )
        return url

    def _post_json(
        self,
        route: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any] | str:
        try:
            response = httpx.post(
                f"{self._storage_base_url}/{route}",
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            return (
                f"Failed to call Pyromind storage {route} API: "
                f"{type(exc).__name__}: {exc}"
            )
        return _decode_json_response(response, f"Pyromind storage {route} API")

    def _download_preview(
        self,
        url: str,
        dataset_path: str,
        file_size: int | None,
        content_type: str,
    ) -> tuple[list[_PreviewChunk], bool] | PreviewDatasetObservation:
        kind = _preview_kind(dataset_path, content_type)
        ranges = _build_preview_ranges(
            file_size=file_size,
            preview_kind=kind,
        )
        chunks: list[_PreviewChunk] = []
        truncated = file_size is None or file_size > _SMALL_FILE_THRESHOLD
        for start, end in ranges:
            chunk_result = self._download_range(url, dataset_path, start, end)
            if isinstance(chunk_result, PreviewDatasetObservation):
                return chunk_result
            chunks.append(
                _PreviewChunk(
                    content=chunk_result,
                    starts_at_zero=start == 0,
                    ends_at_eof=file_size is not None and end >= file_size - 1,
                )
            )
        return chunks, truncated

    def _download_range(
        self,
        url: str,
        dataset_path: str,
        start: int,
        end: int,
    ) -> bytes | PreviewDatasetObservation:
        chunks = bytearray()
        max_bytes = end - start + 1
        headers = {"range": f"bytes={start}-{end}"}
        try:
            with httpx.stream(
                "GET",
                url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
            ) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")
                    return PreviewDatasetObservation.from_text(
                        text=(
                            "Pyromind storage download URL returned HTTP "
                            f"{response.status_code}: {_truncate_text(body)}"
                        ),
                        is_error=True,
                        dataset_path=dataset_path,
                    )
                for chunk in response.iter_bytes():
                    remaining = max_bytes - len(chunks)
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunks.extend(chunk[:remaining])
                        break
                    chunks.extend(chunk)
        except httpx.RequestError as exc:
            return PreviewDatasetObservation.from_text(
                text=(
                    "Failed to download Pyromind storage preview bytes: "
                    f"{type(exc).__name__}: {exc}"
                ),
                is_error=True,
                dataset_path=dataset_path,
            )
        return bytes(chunks)

    def _resolve_headers(
        self,
        conversation: BaseConversation | None,
        *,
        json_content: bool,
    ) -> dict[str, str]:
        headers = {"accept": "*/*", **self._headers}
        if json_content:
            headers["content-type"] = "application/json"
        headers.update(_resolve_conversation_headers(conversation))
        headers.update(_resolve_secret_headers(conversation, self._secret_headers))
        return headers


class PreviewDatasetTool(
    ToolDefinition[PreviewDatasetAction, PreviewDatasetObservation]
):
    """Tool for previewing datasets on Pyromind storage."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[ToolDefinition]:
        storage_base_url = str(
            params.pop("storage_base_url", _default_storage_base_url())
        )
        headers = params.pop("headers", None)
        secret_headers = params.pop("secret_headers", None)
        timeout = float(params.pop("timeout", 30.0))
        max_preview_bytes = int(params.pop("max_preview_bytes", _DEFAULT_PREVIEW_BYTES))
        if params:
            names = ", ".join(sorted(params))
            raise ValueError(f"PreviewDatasetTool got unknown params: {names}")
        _validate_storage_tool_params(
            storage_base_url,
            headers,
            secret_headers,
            timeout,
            max_preview_bytes,
        )
        return [
            cls(
                description=_PREVIEW_DATASET_DESCRIPTION,
                action_type=PreviewDatasetAction,
                observation_type=PreviewDatasetObservation,
                executor=PreviewDatasetExecutor(
                    storage_base_url=storage_base_url,
                    headers=_normalize_headers(headers),
                    secret_headers=_normalize_headers(secret_headers),
                    timeout=timeout,
                    max_preview_bytes=max_preview_bytes,
                ),
                annotations=ToolAnnotations(
                    title="preview_dataset",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


# ---------------------------------------------------------------------------
# upload_file_to_pyromind
# ---------------------------------------------------------------------------


class UploadFileToPyromindAction(Action):
    """Upload a local workspace file to Pyromind storage."""

    file_path: str = Field(
        description=(
            "Path of the file in the current conversation workspace to "
            "upload (e.g. 'acc.py')."
        ),
    )
    target_dir: str = Field(
        default=DEFAULT_UPLOAD_TARGET_DIR,
        description="Storage directory to upload into. Defaults to /agentTest.",
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Upload file to Pyromind: ", style="bold blue")
        content.append(self.file_path)
        return content


class UploadFileToPyromindObservation(Observation):
    """Result of a storage upload."""

    storage_path: str | None = Field(
        default=None,
        description=(
            "Absolute storage path of the uploaded file, usable in node "
            "parameters (e.g. /agentTest/acc.py)."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Upload failed", style="bold red")
        else:
            content.append("Uploaded: ", style="bold green")
            content.append(self.storage_path or "")
        return content


_UPLOAD_FILE_DESCRIPTION = """Upload a workspace file to Pyromind storage.

Use this when a workflow node needs a server-side file path, most commonly a
custom evaluation metric or reward script for MetricsConfigBuilderCustomNode:
write the Python file locally first, upload it with this tool, then use the
returned storage path in the node's `entry` parameter as
`<storage_path>:<function_name>` (e.g. /agentTest/acc.py:acc_func).

Returns the absolute storage path of the uploaded file.
"""


class UploadFileToPyromindExecutor(
    ToolExecutor[UploadFileToPyromindAction, UploadFileToPyromindObservation]
):
    """Upload workspace files through the Pyromind storage API."""

    def __init__(
        self,
        storage_base_url: str | None = None,
        headers: dict[str, str] | None = None,
        secret_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        base_url = storage_base_url or _default_storage_base_url()
        self._storage_base_url = base_url.rstrip("/")
        self._headers = dict(headers or {})
        self._secret_headers = dict(secret_headers or {})
        self._timeout = timeout

    def __call__(
        self,
        action: UploadFileToPyromindAction,
        conversation: BaseConversation | None = None,
    ) -> UploadFileToPyromindObservation:
        try:
            local_path = _resolve_workspace_file(action.file_path, conversation)
            headers = self._resolve_headers(conversation)
        except ValueError as exc:
            return UploadFileToPyromindObservation.from_text(
                text=str(exc),
                is_error=True,
            )

        try:
            storage_path = upload_local_file_to_pyromind(
                local_path=local_path,
                target_dir=action.target_dir,
                storage_base_url=self._storage_base_url,
                headers=headers,
                timeout=self._timeout,
            )
        except ValueError as exc:
            return UploadFileToPyromindObservation.from_text(
                text=str(exc),
                is_error=True,
            )
        except OSError as exc:
            return UploadFileToPyromindObservation.from_text(
                text=f"Failed to read file for upload: {exc}",
                is_error=True,
            )

        return UploadFileToPyromindObservation.from_text(
            text=f"File uploaded to Pyromind storage: {storage_path}",
            storage_path=storage_path,
        )

    def _resolve_headers(
        self,
        conversation: BaseConversation | None,
    ) -> dict[str, str]:
        headers = {"accept": "*/*", **self._headers}
        headers.update(_resolve_conversation_headers(conversation))
        headers.update(_resolve_secret_headers(conversation, self._secret_headers))
        return headers


def upload_local_file_to_pyromind(
    *,
    local_path: Path,
    target_dir: str,
    storage_base_url: str,
    headers: dict[str, str],
    timeout: float,
) -> str:
    """Upload a trusted local runtime file and return its Storage path."""
    filename = local_path.name
    storage_path = str(PurePosixPath(target_dir) / filename)
    try:
        with local_path.open("rb") as file_obj:
            response = httpx.post(
                f"{storage_base_url.rstrip('/')}/upload_file",
                headers=headers,
                data={"name": filename, "path": target_dir, "bucket": ""},
                files={"file": (filename, file_obj, "application/octet-stream")},
                timeout=timeout,
            )
    except httpx.RequestError as exc:
        raise ValueError(
            "Failed to call Pyromind storage upload_file API: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    payload_result = _decode_json_response(response, "Pyromind storage upload_file API")
    if isinstance(payload_result, str):
        raise ValueError(payload_result)
    data_result = _extract_api_data("upload_file", payload_result)
    if isinstance(data_result, str):
        raise ValueError(data_result)

    failed_files = data_result.get("failed_files")
    if isinstance(failed_files, list) and failed_files:
        detail = _truncate_text(json.dumps(failed_files, ensure_ascii=False))
        raise ValueError(
            f"Pyromind storage upload_file API reported failed files: {detail}"
        )
    if data_result.get("success_count") != 1:
        detail = json.dumps(data_result, ensure_ascii=False)
        raise ValueError(
            "Pyromind storage upload_file API did not report one uploaded "
            f"file: {detail}"
        )
    return storage_path


def download_file_from_pyromind(
    *,
    storage_path: str,
    storage_base_url: str,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int,
) -> bytes:
    """Download one bounded storage file for non-data control-plane checks."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be greater than 0")
    try:
        response = httpx.post(
            f"{storage_base_url.rstrip('/')}/get_url",
            headers=headers,
            json={"path": storage_path},
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise ValueError(
            f"Failed to request Pyromind storage download URL: {exc}"
        ) from exc
    payload = _decode_json_response(response, "Pyromind storage get_url API")
    if isinstance(payload, str):
        raise ValueError(payload)
    data = _extract_api_data("get_url", payload)
    if isinstance(data, str):
        raise ValueError(data)
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Pyromind storage get_url API response is missing url data.")

    content = bytearray()
    try:
        with httpx.stream(
            "GET",
            url,
            headers={},
            timeout=timeout,
            follow_redirects=True,
        ) as download:
            if download.status_code >= 400:
                body = download.read().decode("utf-8", errors="replace")
                raise ValueError(
                    "Pyromind storage download URL returned HTTP "
                    f"{download.status_code}: {_truncate_text(body)}"
                )
            for chunk in download.iter_bytes():
                if len(content) + len(chunk) > max_bytes:
                    raise ValueError(
                        f"Storage file exceeds the {max_bytes}-byte preflight limit."
                    )
                content.extend(chunk)
    except httpx.RequestError as exc:
        raise ValueError(f"Failed to download Pyromind storage file: {exc}") from exc
    return bytes(content)


class UploadFileToPyromindTool(
    ToolDefinition[UploadFileToPyromindAction, UploadFileToPyromindObservation]
):
    """Tool for uploading workspace files to Pyromind storage."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[ToolDefinition]:
        storage_base_url = str(
            params.pop("storage_base_url", _default_storage_base_url())
        )
        headers = params.pop("headers", None)
        secret_headers = params.pop("secret_headers", None)
        timeout = float(params.pop("timeout", 30.0))
        if params:
            names = ", ".join(sorted(params))
            raise ValueError(f"UploadFileToPyromindTool got unknown params: {names}")
        _validate_storage_tool_params(
            storage_base_url,
            headers,
            secret_headers,
            timeout,
            _DEFAULT_PREVIEW_BYTES,
        )
        return [
            cls(
                description=_UPLOAD_FILE_DESCRIPTION,
                action_type=UploadFileToPyromindAction,
                observation_type=UploadFileToPyromindObservation,
                executor=UploadFileToPyromindExecutor(
                    storage_base_url=storage_base_url,
                    headers=_normalize_headers(headers),
                    secret_headers=_normalize_headers(secret_headers),
                    timeout=timeout,
                ),
                annotations=ToolAnnotations(
                    title="upload_file_to_pyromind",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


def _validate_storage_tool_params(
    storage_base_url: str,
    headers: Any,
    secret_headers: Any,
    timeout: float,
    max_preview_bytes: int,
) -> None:
    if not storage_base_url.strip():
        raise ValueError("storage_base_url must be a non-empty string")
    if headers is not None and not isinstance(headers, dict):
        raise ValueError("headers must be a dictionary when provided")
    if secret_headers is not None and not isinstance(secret_headers, dict):
        raise ValueError("secret_headers must be a dictionary when provided")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    if max_preview_bytes <= 0:
        raise ValueError("max_preview_bytes must be greater than 0")


def _normalize_headers(value: Any) -> dict[str, str] | None:
    if not value:
        return None
    return {str(k): str(v) for k, v in value.items()}


def _resolve_secret_headers(
    conversation: BaseConversation | None,
    configured_secret_headers: dict[str, str],
) -> dict[str, str]:
    secret_headers = dict(configured_secret_headers)
    if conversation is not None:
        state = cast("ConversationState", conversation.state)
        secret_registry = state.secret_registry
        if secret_registry.get_secret_value(PYROMIND_STORAGE_AUTH_COOKIE_SECRET):
            secret_headers.setdefault("cookie", PYROMIND_STORAGE_AUTH_COOKIE_SECRET)
    if not secret_headers:
        return {}
    if conversation is None:
        raise ValueError(
            "Cannot resolve Pyromind storage API header secrets without an "
            "active conversation."
        )

    resolved: dict[str, str] = {}
    state = cast("ConversationState", conversation.state)
    secret_registry = state.secret_registry
    for header_name, secret_name in secret_headers.items():
        value = secret_registry.get_secret_value(secret_name)
        if not value:
            raise ValueError(
                f"Secret '{secret_name}' required for Pyromind storage API "
                f"header '{header_name}' was not found."
            )
        resolved[header_name] = value
    return resolved


def _resolve_conversation_headers(
    conversation: BaseConversation | None,
) -> dict[str, str]:
    if conversation is None:
        return {}

    state = cast("ConversationState", conversation.state)
    headers = state.agent_state.get(PYROMIND_STORAGE_HEADERS_STATE_KEY)
    if not isinstance(headers, dict):
        return {}
    return {
        str(name): str(value) for name, value in headers.items() if value is not None
    }


def _resolve_workspace_file(
    file_path: str,
    conversation: BaseConversation | None,
) -> Path:
    if conversation is None:
        raise ValueError(
            "Cannot upload a workspace file without an active conversation."
        )

    workspace = cast(Any, conversation).workspace
    workspace_dir = Path(workspace.working_dir).resolve()
    path_policy = default_path_access_policy(workspace_dir)
    candidate = Path(file_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace_dir / candidate).resolve()
    )
    try:
        resolved.relative_to(workspace_dir)
    except ValueError as exc:
        raise ValueError(
            f"Cannot upload file outside the conversation workspace: {file_path}"
        ) from exc
    if not path_policy.check(resolved, "read") or not resolved.is_file():
        raise ValueError(f"Cannot upload missing workspace file: {file_path}")
    return resolved


def _decode_json_response(
    response: httpx.Response,
    api_name: str,
) -> dict[str, Any] | str:
    if response.status_code >= 400:
        return (
            f"{api_name} returned HTTP {response.status_code}: "
            f"{_truncate_text(response.text)}"
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        return f"{api_name} returned invalid JSON: {exc.msg}"
    if not isinstance(payload, dict):
        return f"{api_name} returned a non-object JSON payload."
    return payload


def _extract_api_data(api_name: str, payload: dict[str, Any]) -> dict[str, Any] | str:
    success = payload.get("success")
    data = payload.get("data")
    if success is not True:
        return _format_api_failure(api_name, payload)
    if not isinstance(data, dict):
        return f"Pyromind storage {api_name} API response is missing object data."
    if data.get("isLoggedIn") is False:
        message = _optional_str(data.get("message")) or "login required"
        return f"Pyromind storage {api_name} API requires login: {message}"
    if data.get("uploaded") is False:
        message = _optional_str(data.get("message")) or "upload failed"
        return f"Pyromind storage {api_name} API failed: {message}"
    return data


def _format_api_failure(api_name: str, payload: dict[str, Any]) -> str:
    message = _optional_str(payload.get("message")) or "unknown API failure"
    error_code = _optional_str(payload.get("error_code"))
    if error_code:
        return f"Pyromind storage {api_name} API failed with {error_code}: {message}"
    return f"Pyromind storage {api_name} API failed: {message}"


def _metadata_observation_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = metadata.get("metadata")
    return {
        "object_name": _optional_str(metadata.get("object_name")),
        "bucket_name": _optional_str(metadata.get("bucket_name")),
        "size": _optional_int(metadata.get("size")),
        "content_type": _optional_str(metadata.get("content_type")),
        "is_dir": (
            metadata.get("is_dir") if isinstance(metadata.get("is_dir"), bool) else None
        ),
        "metadata": raw_metadata if isinstance(raw_metadata, dict) else {},
    }


def _looks_like_directory(path: str) -> bool:
    return path.endswith("/")


def _match_shared_dataset(
    dataset_path: str,
    datasets: list[str],
) -> tuple[str, str] | None:
    """Match dataset_path against shared datasets.

    Returns (dataset_name, remaining_file_path) or None if no match.
    Uses longest-prefix matching so 'openai/gsm8k/data/train.jsonl' matches
    dataset 'openai/gsm8k' with file_path 'data/train.jsonl'.
    """
    normalized = dataset_path.strip("/")
    best_match: str | None = None
    for ds in datasets:
        ds_normalized = ds.strip("/")
        if normalized == ds_normalized:
            best_match = ds_normalized
            break
        if normalized.startswith(ds_normalized + "/"):
            if best_match is None or len(ds_normalized) > len(best_match):
                best_match = ds_normalized
    if best_match is None:
        return None
    if normalized == best_match:
        return best_match, ""
    file_path = normalized[len(best_match) + 1 :]
    return best_match, file_path


def _select_preview_file(files: list[str]) -> str | None:
    for file_path in files:
        if PurePosixPath(file_path).suffix.lower() in _SUPPORTED_PREVIEW_SUFFIXES:
            return file_path
    return files[0] if files else None


def _build_preview_ranges(
    *,
    file_size: int | None,
    preview_kind: str | None,
) -> list[tuple[int, int]]:
    if file_size is None or file_size <= _SMALL_FILE_THRESHOLD:
        return [(0, _SMALL_FILE_THRESHOLD - 1)]

    range_size = _LARGE_FILE_RANGE_BYTES
    head_bytes = (
        min(_DELIMITED_HEADER_BYTES, range_size, file_size)
        if preview_kind in {"csv", "tsv"}
        else min(range_size, file_size)
    )
    header_range = [(0, head_bytes - 1)]
    min_sample_start = head_bytes
    max_start = max(min_sample_start, file_size - range_size)
    starts = _random_starts(
        min_sample_start,
        max_start,
        max(1, _LARGE_FILE_RANGE_COUNT - 1),
    )
    ranges = [(start, min(file_size - 1, start + range_size - 1)) for start in starts]
    return header_range + ranges


def _random_starts(start: int, end: int, count: int) -> list[int]:
    if count <= 1 or start >= end:
        return [start]
    starts: set[int] = set()
    attempts = count * 4
    for _ in range(attempts):
        starts.add(random.randint(start, end))
        if len(starts) >= count:
            break
    while len(starts) < count:
        starts.add(start + round((end - start) * len(starts) / count))
    return sorted(starts)


def _parse_preview_chunks(
    file_path: str,
    content_type: str,
    chunks: list[_PreviewChunk],
    *,
    max_samples: int,
    truncated: bool,
) -> dict[str, Any]:
    kind = _preview_kind(file_path, content_type)
    lines = _complete_lines_from_chunks(chunks)
    if kind == "jsonl":
        parsed = _parse_jsonl_lines(lines, max_samples, truncated)
    elif kind in {"csv", "tsv"}:
        delimiter = "\t" if kind == "tsv" else ","
        parsed = _parse_delimited_lines(lines, max_samples, truncated, delimiter)
    else:
        parsed = _parse_raw_text_lines(lines, max_samples, truncated)
    parsed["format_hint"] = _preview_format_hint(kind, chunks)
    return parsed


def _preview_format_hint(
    kind: str | None,
    chunks: list[_PreviewChunk],
) -> str:
    if kind in {"json", "jsonl", "csv", "tsv"}:
        return kind
    head = next((chunk for chunk in chunks if chunk.starts_at_zero), None)
    if head is not None:
        prefix = head.content.decode("utf-8-sig", errors="replace").lstrip()
        if prefix.startswith(("{", "[")):
            return "json-like"
    return kind or "text"


def _parse_jsonl_lines(
    lines: list[str],
    sample_limit: int,
    truncated: bool,
) -> dict[str, Any]:
    non_empty = [line for line in lines if line.strip()]
    indexed = list(enumerate(non_empty, start=1))
    sampled = indexed[:sample_limit]
    sample_rows: list[dict[str, Any]] = []
    for idx, line in sampled:
        row: dict[str, Any] = {
            "line": idx,
            "text": _truncate_text(line, _MAX_SAMPLE_STRING_CHARS),
        }
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                row.update(_trim_dict(parsed))
            else:
                row["value"] = _trim_value(parsed)
        except json.JSONDecodeError:
            pass
        sample_rows.append(row)
    previewed_rows = len(non_empty)
    return {
        "num_rows": previewed_rows if not truncated else None,
        "columns": _collect_columns(sample_rows),
        "sample_rows": sample_rows,
        "previewed_rows": previewed_rows,
        "preview_error": None,
    }


def _parse_delimited_lines(
    lines: list[str],
    sample_limit: int,
    truncated: bool,
    delimiter: str,
) -> dict[str, Any]:
    if not lines or delimiter not in lines[0]:
        return _parse_raw_text_lines(lines, sample_limit, truncated)

    header = lines[0]
    data_lines = lines[1:]
    sampled_data = data_lines[:sample_limit]
    reader = csv.DictReader([header, *sampled_data], delimiter=delimiter)
    columns = [str(name) for name in reader.fieldnames or []]
    sample_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(reader, start=1):
        raw_text = _truncate_text(
            sampled_data[idx - 1] if idx - 1 < len(sampled_data) else "",
            _MAX_SAMPLE_STRING_CHARS,
        )
        entry: dict[str, Any] = {"line": idx, "text": raw_text}
        for key, value in row.items():
            if key is not None and value is not None:
                entry[key] = _truncate_text(str(value), _MAX_SAMPLE_STRING_CHARS)
        sample_rows.append(entry)
    previewed_rows = max(len(lines) - 1, 0)
    return {
        "num_rows": previewed_rows if not truncated else None,
        "columns": columns,
        "sample_rows": sample_rows,
        "previewed_rows": previewed_rows,
        "preview_error": None,
    }


def _parse_raw_text_lines(
    lines: list[str],
    sample_limit: int,
    truncated: bool,
) -> dict[str, Any]:
    indexed = list(enumerate(lines, start=1))
    sampled = indexed[:sample_limit]
    sample_rows = [
        {"line": idx, "text": _truncate_text(text, _MAX_SAMPLE_STRING_CHARS)}
        for idx, text in sampled
    ]
    previewed_rows = len(lines)
    return {
        "num_rows": previewed_rows if not truncated else None,
        "columns": [],
        "sample_rows": sample_rows,
        "previewed_rows": previewed_rows,
        "preview_error": None,
    }


def _preview_kind(file_path: str, content_type: str) -> str | None:
    suffix = PurePosixPath(file_path).suffix.lower()
    normalized_content_type = content_type.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".json" or "json" in normalized_content_type:
        return "json"
    if suffix == ".csv" or "csv" in normalized_content_type:
        return "csv"
    if suffix == ".tsv":
        return "tsv"
    if suffix in _TEXT_PREVIEW_SUFFIXES or normalized_content_type.startswith("text/"):
        return "text"
    return None


def _complete_lines_from_chunks(chunks: list[_PreviewChunk]) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        text = chunk.content.decode("utf-8", errors="replace")
        lines.extend(text.splitlines())
    return lines


def _trim_value(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, _MAX_SAMPLE_STRING_CHARS)
    if isinstance(value, list):
        return [_trim_value(item) for item in value]
    if isinstance(value, dict):
        return _trim_dict(value)
    return value


def _trim_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _trim_value(item) for key, item in value.items()}


def _collect_columns(sample_rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in sample_rows:
        for key in row:
            if key in seen:
                continue
            seen.add(key)
            columns.append(key)
    return columns


def _infer_has_vision(
    columns: list[str],
    sample_rows: list[dict[str, Any]],
) -> bool | None:
    if not columns and not sample_rows:
        return None
    for column in columns:
        if any(marker in column.lower() for marker in _VISION_FIELD_MARKERS):
            return True
    for row in sample_rows:
        for value in row.values():
            if not isinstance(value, str):
                continue
            if any(
                PurePosixPath(part.split("?", 1)[0]).suffix.lower() in _VISION_SUFFIXES
                for part in value.split()
            ):
                return True
    return False


def _trim_sample_rows(
    sample_rows: list[dict[str, Any]],
    max_bytes: int,
) -> list[dict[str, Any]]:
    rows = list(sample_rows)
    while rows:
        lines = [str(row.get("text", "")) for row in rows if row.get("text")]
        content = "\n".join(lines) + ("\n" if lines else "")
        if len(content.encode("utf-8")) <= max_bytes:
            return rows
        rows = rows[:-1]
    return []


def _format_preview_text(
    *,
    dataset_path: str,
    preview_path: str,
    files: list[str],
    metadata: dict[str, Any],
    parsed: dict[str, Any],
    requested_rows: int,
    preview_truncated: bool,
) -> str:
    fields = _metadata_observation_fields(metadata)
    parts = [
        f"Dataset preview: {dataset_path}",
        f"preview_file_path={preview_path}",
        f"files={len(files)}",
    ]
    if fields["size"] is not None:
        parts.append(f"size={fields['size']} bytes")
    if parsed["num_rows"] is not None:
        parts.append(f"rows={parsed['num_rows']}")
    else:
        parts.append(f"previewed_rows={parsed['previewed_rows']}")
    if parsed["columns"]:
        parts.append(f"columns={', '.join(parsed['columns'])}")
    parts.append(f"requested_rows={requested_rows}")
    parts.append(f"sample_rows={len(parsed['sample_rows'])}")
    if parsed["preview_error"]:
        parts.append(f"preview_error={parsed['preview_error']}")
    parts.append(f"format_hint={parsed['format_hint']}")
    parts.append(f"preview_truncated={str(preview_truncated).lower()}")
    if preview_truncated:
        parts.append("sample_integrity=partial_byte_fragments")
        parts.append(
            "warning=sample lines may start or end inside a source record; "
            "use them only as a format hint"
        )
    for row in parsed["sample_rows"]:
        parts.append(f"\n--- sample line {row.get('line', '?')} ---")
        parts.append(str(row.get("text", "")))
    return "\n".join(parts)


def _format_shared_preview_text(
    *,
    dataset_name: str,
    file_path: str,
    data: dict[str, Any],
    parsed: dict[str, Any],
    requested_rows: int,
    truncated: bool,
) -> str:
    parts = [
        f"Shared dataset preview: {dataset_name}",
        f"file={file_path}",
    ]
    human_size = data.get("human_size")
    if human_size:
        parts.append(f"size={human_size}")
    elif data.get("file_size") is not None:
        parts.append(f"size={data['file_size']} bytes")
    if parsed["num_rows"] is not None:
        parts.append(f"rows={parsed['num_rows']}")
    else:
        parts.append(f"previewed_rows={parsed['previewed_rows']}")
    if parsed["columns"]:
        parts.append(f"columns={', '.join(parsed['columns'])}")
    parts.append(f"requested_rows={requested_rows}")
    parts.append(f"sample_rows={len(parsed['sample_rows'])}")
    if parsed["preview_error"]:
        parts.append(f"preview_error={parsed['preview_error']}")
    parts.append(f"truncated={str(truncated).lower()}")
    for row in parsed["sample_rows"]:
        parts.append(f"\n--- sample line {row.get('line', '?')} ---")
        parts.append(str(row.get("text", "")))
    return "\n".join(parts)


def _resolve_workspace_dir(
    conversation: BaseConversation | None,
) -> Path:
    if conversation is None:
        raise ValueError(
            "Sample materialization requires an active conversation workspace."
        )
    workspace = cast(Any, conversation).workspace
    workspace_dir = Path(workspace.working_dir).resolve()
    path_policy = default_path_access_policy(workspace_dir)
    sample_root = workspace_dir / "public_data" / "data-preparation"
    if not path_policy.check(sample_root, "write"):
        raise ValueError(
            "Conversation public_data/data-preparation directory is not writable."
        )
    return workspace_dir


def _normalize_storage_sample_path(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("Sample path must be non-empty.")
    pure_path = PurePosixPath("/" + raw.lstrip("/"))
    if ".." in pure_path.parts:
        raise ValueError("Sample path must not contain '..'.")
    normalized = pure_path.as_posix()
    if normalized == "/":
        raise ValueError("Storage root cannot be materialized as a sample.")
    return normalized


def _sample_local_file(preview_root: Path, storage_path: str) -> Path:
    normalized = _normalize_storage_sample_path(storage_path)
    relative = PurePosixPath(normalized.lstrip("/"))
    local_file = preview_root.joinpath(*relative.parts).resolve()
    try:
        local_file.relative_to(preview_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Unsafe storage sample path: {storage_path}") from exc
    return local_file


def _storage_entry_dict(entry: _StorageFileInfo) -> dict[str, Any]:
    return {
        "path": entry.path,
        "name": entry.name,
        "type": "folder" if entry.is_dir else "file",
        "size": entry.size,
        "last_modified": entry.last_modified,
    }


def _empty_directory_summary() -> dict[str, Any]:
    return {
        "top_level_folder_count": 0,
        "top_level_file_count": 0,
        "top_level_suffix_counts": {},
        "top_level_type_counts": {
            "image": 0,
            "video": 0,
            "text": 0,
            "table": 0,
            "json": 0,
            "other": 0,
        },
        "sampled_child_folders": [],
        "child_listing_errors": [],
        "repeated_file_name_set": None,
        "repeated_suffix_set": None,
        "detected_layout": "unknown",
        "layout_confidence": "low",
        "layout_evidence": "directory is empty",
    }


def _suffix_counts(entries: list[_StorageFileInfo]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        suffix = PurePosixPath(entry.path).suffix.lower() or "<none>"
        counts[suffix] = counts.get(suffix, 0) + 1
    return dict(sorted(counts.items()))


def _type_counts(suffix_counts: dict[str, int]) -> dict[str, int]:
    result = {
        "image": 0,
        "video": 0,
        "text": 0,
        "table": 0,
        "json": 0,
        "other": 0,
    }
    for suffix, count in suffix_counts.items():
        if suffix in _IMAGE_SUFFIXES:
            result["image"] += count
        elif suffix in {".mp4", ".mov", ".avi", ".webm"}:
            result["video"] += count
        elif suffix in _TEXT_PREVIEW_SUFFIXES:
            result["text"] += count
        elif suffix in {".csv", ".tsv"}:
            result["table"] += count
        elif suffix in {".json", ".jsonl"}:
            result["json"] += count
        else:
            result["other"] += count
    return result


def _shared_child_value(
    sampled_child_folders: list[dict[str, Any]],
    key: str,
) -> Any | None:
    values = [folder.get(key) for folder in sampled_child_folders]
    if not values:
        return None
    first = values[0]
    if all(value == first for value in values):
        return first
    return None


def _infer_directory_layout(
    entries: list[_StorageFileInfo],
    directory_summary: dict[str, Any],
) -> dict[str, Any]:
    folders = [entry for entry in entries if entry.is_dir]
    files = [entry for entry in entries if not entry.is_dir]
    sampled_folders = directory_summary.get("sampled_child_folders") or []
    repeated_names = directory_summary.get("repeated_file_name_set")
    repeated_suffixes = directory_summary.get("repeated_suffix_set")
    top_type_counts = directory_summary.get("top_level_type_counts") or {}

    if folders and len(sampled_folders) >= 2 and (repeated_names or repeated_suffixes):
        sampled_count = min(len(folders), _MAX_DIRECTORY_CHILD_SAMPLES)
        if repeated_names:
            evidence = (
                f"{len(sampled_folders)}/{sampled_count} "
                "sampled child folders share files: "
                f"{', '.join(repeated_names)}"
            )
        else:
            suffix_text = ", ".join(
                f"{suffix}:{count}" for suffix, count in repeated_suffixes.items()
            )
            evidence = (
                f"{len(sampled_folders)}/{sampled_count} "
                f"sampled child folders share suffix counts: {suffix_text}"
            )
        return {
            "detected_layout": "repeated_sample_folders",
            "layout_confidence": "high",
            "layout_evidence": evidence,
        }

    if not folders and files:
        if top_type_counts.get("table", 0) or top_type_counts.get("json", 0):
            evidence = (
                f"top-level files include table/json data; suffixes="
                f"{directory_summary.get('top_level_suffix_counts') or {}}"
            )
            return {
                "detected_layout": "tabular_or_manifest_files",
                "layout_confidence": "medium",
                "layout_evidence": evidence,
            }
        evidence = (
            f"directory contains {len(files)} top-level files and no folders; "
            f"suffixes={directory_summary.get('top_level_suffix_counts') or {}}"
        )
        return {
            "detected_layout": "flat_file_collection",
            "layout_confidence": "medium",
            "layout_evidence": evidence,
        }

    if folders and files:
        return {
            "detected_layout": "mixed_directory",
            "layout_confidence": "low",
            "layout_evidence": (
                f"directory mixes {len(folders)} top-level folders and "
                f"{len(files)} top-level files without a repeated sampled structure"
            ),
        }

    if folders:
        return {
            "detected_layout": "unknown",
            "layout_confidence": "low",
            "layout_evidence": (
                f"directory contains {len(folders)} folders, but sampled child "
                "folders do not share a clear structure"
            ),
        }

    return {
        "detected_layout": "unknown",
        "layout_confidence": "low",
        "layout_evidence": "directory is empty or structure could not be inferred",
    }


def _is_vision_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _VISION_SUFFIXES


def _is_image_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _IMAGE_SUFFIXES


def _vision_content_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")


def _vision_api_config() -> tuple[str, str, str | None]:
    api_url = os.environ.get("DF_API_URL", "").strip()
    if not api_url:
        base_url = os.environ.get("DF_API_BASE_URL", "").strip().rstrip("/")
        if base_url:
            api_url = f"{base_url}/chat/completions"
    model = os.environ.get("DF_MODEL_NAME", "").strip()
    api_key = os.environ.get("DF_API_KEY")
    if not api_url or not model:
        raise ValueError(
            "Vision preview is not configured; set DF_API_URL (or "
            "DF_API_BASE_URL) and DF_MODEL_NAME."
        )
    return api_url, model, api_key


def _call_vision_preview_model(
    *,
    source_path: str,
    content: bytes,
    timeout: float,
) -> str:
    api_url, model, api_key = _vision_api_config()
    encoded = base64.b64encode(content).decode("ascii")
    data_url = f"data:{_vision_content_type(source_path)};base64,{encoded}"
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Inspect this data-preparation sample. Return a concise "
                            "plain-text description containing: (1) all readable "
                            "OCR text, (2) the visual structure and relationships, "
                            "and (3) any ambiguity or unreadable region. Do not "
                            "invent missing text."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ],
        "temperature": 0,
    }
    attempts = max(1, min(int(os.environ.get("DF_VISION_MAX_ATTEMPTS", "3")), 5))
    vision_timeout = max(
        timeout,
        float(os.environ.get("DF_VISION_TIMEOUT_SECONDS", "300")),
    )
    last_error = "unknown vision preview error"
    for attempt in range(attempts):
        try:
            response = httpx.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=vision_timeout,
            )
        except httpx.RequestError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                decoded = _decode_json_response(response, "Vision preview API")
                if not isinstance(decoded, str):
                    choices = decoded.get("choices")
                    if isinstance(choices, list) and choices:
                        message = choices[0].get("message")
                        if isinstance(message, dict):
                            result = message.get("content")
                            if isinstance(result, str) and result.strip():
                                return result.strip()
                last_error = "Vision preview API response is missing message content."
            else:
                last_error = (
                    f"Vision preview API returned HTTP {response.status_code}: "
                    f"{_truncate_text(response.text)}"
                )
                retryable = (
                    response.status_code in _VISION_RETRYABLE_STATUS_CODES
                    or response.status_code >= 500
                )
                if not retryable:
                    raise ValueError(last_error)
        if attempt + 1 < attempts:
            time.sleep((2**attempt) + random.random())
    raise ValueError(f"Vision preview failed after {attempts} attempt(s): {last_error}")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate_text(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [{len(text) - limit} characters truncated]"


def _human_size(size: int | None) -> str:
    if size is None:
        return "size unknown"
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    return f"{size / (1024 * 1024 * 1024):.2f}GB"


register_tool(PreviewDatasetTool.name, PreviewDatasetTool)
register_tool(UploadFileToPyromindTool.name, UploadFileToPyromindTool)
