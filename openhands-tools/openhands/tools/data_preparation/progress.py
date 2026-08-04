"""Check live progress of an async DataFlow pipeline run on Pyromind Storage.

Reads the ``progress.json`` snapshot (written by the pipeline after each
batch) plus the tail of ``processed.jsonl`` from a run's output directory,
so the agent can report progress, ETA, and recent output quality while the
platform job is still running.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

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
from openhands.tools.pyromind_dataset.definition import (
    _decode_json_response,
    _default_storage_base_url,
    _extract_api_data,
    _normalize_headers,
    _resolve_conversation_headers,
    _resolve_secret_headers,
    _truncate_text,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


PROGRESS_FILENAME = "progress.json"
OUTPUT_FILENAME = "processed.jsonl"
DEFAULT_TAIL_LINES = 5
DEFAULT_TAIL_BYTES = 64 * 1024

TOOL_DESCRIPTION = """\
Check the live progress of an asynchronous DataFlow pipeline run.

Pass the `output_dir` returned by `df_submit_pipeline`. The tool reads the
run's `progress.json` snapshot (total / processed / succeeded / failed /
ETA) and the most recent records from `processed.jsonl`, so you can report
progress and verify output quality while the platform job is still running.

Use this when the user asks about the status of a submitted pipeline, or
proactively between submission and the terminal Kafka callback for
long-running jobs.
"""


# ---------------------------------------------------------------------------
# Action / Observation
# ---------------------------------------------------------------------------


class DfCheckProgressAction(Action):
    """Check progress of a DataFlow pipeline run."""

    output_dir: str = Field(
        description=(
            "Storage output directory of the run, e.g. "
            "'/.pyromind-agent/<conversation_id>/data_preparation/<run_id>' "
            "(the `output_dir` returned by df_submit_pipeline)."
        )
    )
    tail_lines: int = Field(
        default=DEFAULT_TAIL_LINES,
        ge=1,
        le=50,
        description="Number of most recent processed records to show.",
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Check pipeline progress: ", style="bold blue")
        content.append(self.output_dir)
        return content


class DfCheckProgressObservation(Observation):
    """Progress snapshot and recent output records of a pipeline run."""

    output_dir: str = Field(description="The run output directory that was checked.")
    progress_found: bool = Field(
        default=False, description="Whether progress.json was found and parsed."
    )
    total: int | None = Field(default=None)
    processed: int | None = Field(default=None)
    succeeded: int | None = Field(default=None)
    failed: int | None = Field(default=None)
    elapsed_ms: int | None = Field(default=None)
    records_per_second: float | None = Field(default=None)
    eta_ms: int | None = Field(default=None)
    updated_at: str | None = Field(default=None)
    percent: float | None = Field(
        default=None, description="processed / total as a percentage (0-100)."
    )
    latest_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Most recent successfully processed records.",
    )

    @property
    def visualize(self) -> Text:
        text = Text()
        if self.percent is not None:
            text.append(f"Pipeline progress: {self.percent:.1f}%\n", style="bold cyan")
        text.append(self.text)
        return text


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class DfCheckProgressExecutor(
    ToolExecutor[DfCheckProgressAction, DfCheckProgressObservation]
):
    """Read progress.json and the processed.jsonl tail from Storage."""

    def __init__(
        self,
        *,
        storage_base_url: str | None = None,
        headers: dict[str, str] | None = None,
        secret_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        tail_bytes: int = DEFAULT_TAIL_BYTES,
    ) -> None:
        self._storage_base_url = (
            storage_base_url or _default_storage_base_url()
        ).rstrip("/")
        self._headers = dict(headers or {})
        self._secret_headers = dict(secret_headers or {})
        self._timeout = timeout
        self._tail_bytes = tail_bytes

    def __call__(
        self,
        action: DfCheckProgressAction,
        conversation: BaseConversation | None = None,
    ) -> DfCheckProgressObservation:
        output_dir = action.output_dir.strip().strip("/")
        headers = self._resolved_headers(conversation)

        progress_path = f"{output_dir}/{PROGRESS_FILENAME}"
        processed_path = f"{output_dir}/{OUTPUT_FILENAME}"

        progress = self._read_json_file(progress_path, headers)
        latest, tail_error = self._read_tail(processed_path, headers, action.tail_lines)

        fields: dict[str, Any] = {
            "output_dir": output_dir,
            "latest_records": latest,
        }
        percent: float | None = None
        if isinstance(progress, dict):
            fields["progress_found"] = True
            fields["total"] = _optional_int(progress.get("total"))
            fields["processed"] = _optional_int(progress.get("processed"))
            fields["succeeded"] = _optional_int(progress.get("succeeded"))
            fields["failed"] = _optional_int(progress.get("failed"))
            fields["elapsed_ms"] = _optional_int(progress.get("elapsed_ms"))
            fields["records_per_second"] = _optional_float(
                progress.get("records_per_second")
            )
            fields["eta_ms"] = _optional_int(progress.get("eta_ms"))
            fields["updated_at"] = _optional_str(progress.get("updated_at"))
            total = fields["total"]
            processed = fields["processed"]
            if total and processed is not None and total > 0:
                percent = min(100.0, processed / total * 100.0)
            fields["percent"] = percent
        else:
            fields["progress_found"] = False

        text = self._format_text(fields, progress, tail_error)
        return DfCheckProgressObservation.from_text(text=text, **fields)

    # -- Storage primitives -------------------------------------------------

    def _resolved_headers(
        self, conversation: BaseConversation | None
    ) -> dict[str, str]:
        headers = {"accept": "*/*", **self._headers}
        headers.update(_resolve_conversation_headers(conversation))
        headers.update(_resolve_secret_headers(conversation, self._secret_headers))
        return headers

    def _post_json(
        self, route: str, body: dict[str, Any], headers: dict[str, str]
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

    def _get_size_and_url(
        self, path: str, headers: dict[str, str]
    ) -> tuple[int | None, str] | str:
        size = self._live_size(path, headers)
        if size is None:
            # file_list could not see the file: fall back to HEAD metadata so
            # a missing file still surfaces a real error (the size may be a
            # stale cached value, but the caller then reports not-found).
            meta_result = self._post_json("get_file_metadata", {"path": path}, headers)
            if isinstance(meta_result, str):
                return meta_result
            meta = _extract_api_data("get_file_metadata", meta_result)
            if isinstance(meta, str):
                return meta
            size = _optional_int(meta.get("size"))

        url_result = self._post_json("get_url", {"path": path}, headers)
        if isinstance(url_result, str):
            return url_result
        url_data = _extract_api_data("get_url", url_result)
        if isinstance(url_data, str):
            return url_data
        url = url_data.get("url")
        if not isinstance(url, str) or not url.strip():
            return f"Pyromind storage get_url API returned no url for {path}."
        return size, url

    def _live_size(self, path: str, headers: dict[str, str]) -> int | None:
        # file_list reflects live gateway metadata, while the HEAD-based
        # get_file_metadata API can serve a stale cached size for frequently
        # overwritten files — prefer it when the file is visible.
        parent = posixpath.dirname(path)
        name = posixpath.basename(path)
        result = self._post_json("file_list", {"path": parent}, headers)
        if isinstance(result, str):
            return None
        payload = _extract_api_data("file_list", result)
        if isinstance(payload, str) or not isinstance(payload, dict):
            return None
        entries = payload.get("list")
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") == name:
                return _optional_int(entry.get("size"))
        return None

    def _download_range(
        self, url: str, start: int | None, end: int | None
    ) -> bytes | str:
        headers: dict[str, str] = {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if start is not None and end is not None:
            headers["range"] = f"bytes={start}-{end}"
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
                    return (
                        "Pyromind storage download returned HTTP "
                        f"{response.status_code}: {_truncate_text(body)}"
                    )
                return response.read()
        except httpx.RequestError as exc:
            return (
                f"Failed to download from Pyromind storage: {type(exc).__name__}: {exc}"
            )

    # -- File readers -------------------------------------------------------

    def _read_json_file(
        self, path: str, headers: dict[str, str]
    ) -> dict[str, Any] | None:
        result = self._get_size_and_url(path, headers)
        if isinstance(result, str):
            return None
        size, url = result
        # Prefer a size-derived Range: it forces the CDN/gateway to revalidate
        # against the live object (a plain full GET can be served from a stale
        # cache), and the size now comes from file_list (live metadata), not
        # the HEAD API's cached attr. A full GET remains as a fallback.
        parsed = None
        if size is not None and size > 0:
            content = self._download_range(url, 0, size - 1)
            if not isinstance(content, str):
                parsed = self._try_parse_json(content)
        if parsed is None:
            content = self._download_range(url, None, None)
            if not isinstance(content, str):
                parsed = self._try_parse_json(content)
        return parsed

    @staticmethod
    def _try_parse_json(data: bytes) -> dict[str, Any] | None:
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _read_tail(
        self, path: str, headers: dict[str, str], tail_lines: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        result = self._get_size_and_url(path, headers)
        if isinstance(result, str):
            return [], result
        size, url = result
        if size is None or size <= 0:
            return [], None

        start = max(0, size - self._tail_bytes)
        content = self._download_range(url, start, size - 1)
        if isinstance(content, str):
            return [], content

        text = content.decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()]
        # The first line may be a partial fragment when start > 0; drop it.
        if start > 0 and lines:
            lines = lines[1:]
        records: list[dict[str, Any]] = []
        for line in lines[-tail_lines:]:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records, None

    # -- Formatting ---------------------------------------------------------

    def _format_text(
        self,
        fields: dict[str, Any],
        progress: dict[str, Any] | None,
        tail_error: str | None,
    ) -> str:
        parts: list[str] = []
        if progress is None:
            parts.append(
                "progress.json not found yet — the pipeline may not have "
                "started processing. Try again shortly."
            )
        else:
            percent = fields.get("percent")
            pct_text = f" ({percent:.1f}%)" if percent is not None else ""
            parts.append(
                f"Progress: {fields.get('processed')}/{fields.get('total')}{pct_text}"
            )
            parts.append(
                f"succeeded={fields.get('succeeded')} failed={fields.get('failed')}"
            )
            timing = []
            if fields.get("elapsed_ms") is not None:
                timing.append(f"elapsed={_format_ms(fields['elapsed_ms'])}")
            if fields.get("records_per_second") is not None:
                timing.append(f"rate={fields['records_per_second']} rec/s")
            if fields.get("eta_ms") is not None:
                timing.append(f"ETA≈{_format_ms(fields['eta_ms'])}")
            if timing:
                parts.append(", ".join(timing))
            if fields.get("updated_at"):
                parts.append(f"updated_at={fields['updated_at']}")

        records = fields.get("latest_records") or []
        if records:
            parts.append("")
            parts.append(f"Latest {len(records)} processed records:")
            for record in records:
                parts.append(_truncate_text(json.dumps(record, ensure_ascii=False)))
        elif tail_error:
            parts.append(f"\nCould not read processed output: {tail_error}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------


class DfCheckProgressTool(
    ToolDefinition[DfCheckProgressAction, DfCheckProgressObservation]
):
    """Tool definition for checking DataFlow pipeline progress."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[Self]:
        storage_base_url_value = params.pop("storage_base_url", None)
        storage_base_url = (
            str(storage_base_url_value) if storage_base_url_value is not None else None
        )
        headers = _normalize_headers(params.pop("headers", None))
        secret_headers = _normalize_headers(
            params.pop("storage_secret_headers", params.pop("secret_headers", None))
        )
        timeout = float(params.pop("timeout", 30.0))
        if params:
            names = ", ".join(sorted(params))
            raise ValueError(f"DfCheckProgressTool got unknown params: {names}")
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=DfCheckProgressAction,
                observation_type=DfCheckProgressObservation,
                executor=DfCheckProgressExecutor(
                    storage_base_url=storage_base_url,
                    headers=headers,
                    secret_headers=secret_headers,
                    timeout=timeout,
                ),
                annotations=ToolAnnotations(
                    title="df_check_progress",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _optional_str(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _format_ms(ms: int) -> str:
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins}m"


register_tool("df_check_progress", DfCheckProgressTool)
