"""Shared retry and checkpoint helpers for data-preparation pipelines."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import random
import sys
import time
import types
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


try:
    from datetime import UTC as _UTC
except ImportError:
    from datetime import timezone

    _UTC = timezone.utc  # noqa: UP017


RETRYABLE_STATUS_CODES = {408, 409, 425, 429}


@dataclass(frozen=True)
class Checkpoint:
    next_source_index: int = 0
    committed_records: int = 0
    output_offset: int = 0


def load_checkpoint(state_dir: str | Path) -> Checkpoint:
    path = Path(state_dir) / "checkpoint.json"
    if not path.is_file():
        return Checkpoint()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(
            next_source_index=int(payload["next_source_index"]),
            committed_records=int(payload["committed_records"]),
            output_offset=int(payload["output_offset"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid checkpoint file {path}: {exc}") from exc


def prepare_resume_output(
    output_path: str | Path,
    checkpoint: Checkpoint,
) -> None:
    path = Path(output_path)
    if checkpoint.output_offset == 0:
        return
    if not path.is_file():
        raise ValueError("Checkpoint references a missing output JSONL file.")
    current_size = path.stat().st_size
    if current_size < checkpoint.output_offset:
        raise ValueError("Output JSONL is shorter than the checkpoint output offset.")
    if current_size > checkpoint.output_offset:
        with path.open("r+b") as output:
            output.truncate(checkpoint.output_offset)


def commit_record(
    *,
    output_path: str | Path,
    state_dir: str | Path,
    source_index: int,
    row: dict[str, Any],
) -> Checkpoint:
    output = Path(output_path)
    state = Path(state_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)

    encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    with output.open("ab") as target:
        target.write(encoded)
        target.flush()
        os.fsync(target.fileno())
        output_offset = target.tell()

    checkpoint = Checkpoint(
        next_source_index=source_index + 1,
        committed_records=source_index + 1,
        output_offset=output_offset,
    )
    _atomic_json_write(state / "checkpoint.json", asdict(checkpoint))
    return checkpoint


def write_failure_report(
    *,
    state_dir: str | Path,
    source_index: int,
    source_id: str | None,
    source_path: str | None,
    stage: str,
    error: Exception,
    attempts: int,
    retriable: bool,
) -> None:
    checkpoint = load_checkpoint(state_dir)
    report = {
        "status": "failed",
        "counts": {
            "committed": checkpoint.committed_records,
            "attempted": source_index + 1,
        },
        "checkpoint": asdict(checkpoint),
        "failure": {
            "source_index": source_index,
            "source_id": source_id,
            "source_path": source_path,
            "stage": stage,
            "error_type": type(error).__name__,
            "error": _safe_error(str(error)),
            "attempts": attempts,
            "retriable": retriable,
        },
    }
    _atomic_json_write(Path(state_dir) / "failure.json", report)


def write_progress(
    progress_path: str | Path,
    *,
    total: int,
    processed: int,
    succeeded: int,
    failed: int,
    started_at: float,
    attempted_this_run: int,
) -> None:
    """Write a ``progress.json`` snapshot consumed by ``df_check_progress``.

    ``started_at`` is a ``time.monotonic()`` baseline and ``attempted_this_run``
    counts records attempted since that baseline, so the derived rate and ETA
    reflect the current run rather than any resumed prefix.

    Uses ``_atomic_json_write`` (tmp + fsync + rename) so that JuiceFS persists
    the data to its local cache before the atomic rename — the Storage API
    always sees a consistent file with the correct size.
    """
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    elapsed_s = elapsed_ms / 1000
    rate = (
        attempted_this_run / elapsed_s
        if elapsed_s > 0 and attempted_this_run > 0
        else 0.0
    )
    remaining = max(total - processed, 0)
    eta_ms = int(remaining / rate * 1000) if rate > 0 else None
    payload = {
        "total": total,
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "elapsed_ms": elapsed_ms,
        "records_per_second": round(rate, 2),
        "eta_ms": eta_ms,
        "updated_at": datetime.now(_UTC).isoformat(),
    }
    _atomic_json_write(Path(progress_path), payload)


class RetryingVisionClient:
    """Small OpenAI-compatible vision client with explicit retry semantics."""

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.api_url = (
            api_url
            or os.environ.get("DF_API_URL")
            or _chat_url(os.environ.get("DF_API_BASE_URL"))
        )
        self.api_key = api_key or os.environ.get("DF_API_KEY")
        self.model = model or os.environ.get("DF_MODEL_NAME")
        self.timeout = timeout or float(
            os.environ.get("DF_VISION_TIMEOUT_SECONDS", "300")
        )
        self.max_attempts = max_attempts or int(
            os.environ.get("DF_VISION_MAX_ATTEMPTS", "3")
        )
        if not self.api_url or not self.model:
            raise ValueError(
                "DF_API_URL (or DF_API_BASE_URL) and DF_MODEL_NAME are required."
            )
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")

    def generate(
        self,
        *,
        prompt: str,
        image_paths: list[str | Path],
        validate: Callable[[str], Any] | None = None,
    ) -> tuple[str, int]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            path = Path(image_path)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{_image_mime(path)};base64,{encoded}"},
                }
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        last_error: Exception = ValueError("Model did not return a response.")
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                response = httpx.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    error = ValueError(
                        f"Vision API returned HTTP {response.status_code}: "
                        f"{response.text[:1000]}"
                    )
                    if not _retryable_status(response.status_code):
                        _log_vision_attempt(
                            attempt=attempt,
                            model=str(self.model),
                            prompt=prompt,
                            image_count=len(image_paths),
                            status="error",
                            latency_ms=(time.monotonic() - started) * 1000,
                            error=str(error),
                        )
                        raise error
                    last_error = error
                else:
                    try:
                        result = _response_text(response)
                    except ValueError as exc:
                        last_error = exc
                        result = None
                    if result is None:
                        pass
                    if validate is not None and result is not None:
                        try:
                            validate(result)
                        except (TypeError, ValueError) as exc:
                            last_error = exc
                            payload["messages"].extend(
                                [
                                    {"role": "assistant", "content": result},
                                    {
                                        "role": "user",
                                        "content": (
                                            "The previous output failed validation: "
                                            f"{exc}. Return a corrected answer only."
                                        ),
                                    },
                                ]
                            )
                        else:
                            _log_vision_attempt(
                                attempt=attempt,
                                model=str(self.model),
                                prompt=prompt,
                                image_count=len(image_paths),
                                status="success",
                                latency_ms=(time.monotonic() - started) * 1000,
                            )
                            return result, attempt
                    elif validate is None and result is not None:
                        _log_vision_attempt(
                            attempt=attempt,
                            model=str(self.model),
                            prompt=prompt,
                            image_count=len(image_paths),
                            status="success",
                            latency_ms=(time.monotonic() - started) * 1000,
                        )
                        return result, attempt
            except httpx.RequestError as exc:
                last_error = exc
            _log_vision_attempt(
                attempt=attempt,
                model=str(self.model),
                prompt=prompt,
                image_count=len(image_paths),
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                error=f"{type(last_error).__name__}: {last_error}",
            )
            if attempt < self.max_attempts:
                time.sleep((2 ** (attempt - 1)) + random.random())
        raise ValueError(
            f"Model generation failed after {self.max_attempts} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error


class RetryingChatClient(RetryingVisionClient):
    """OpenAI-compatible text client sharing the same retry and logging policy."""

    def generate(
        self,
        *,
        prompt: str,
        validate: Callable[[str], Any] | None = None,
    ) -> tuple[str, int]:
        return super().generate(
            prompt=prompt,
            image_paths=[],
            validate=validate,
        )


def _chat_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/chat/completions"


def create_text_serving() -> Any:
    """Build the batch text LLM serving used by text pipelines.

    Loads DataFlow's ``APILLMServing_request`` (its ``generate_from_input`` fans a
    batch out across an internal thread pool with built-in retries and connect/read
    timeouts) and wraps it in ``LoggingLLMServing`` so every per-item call is
    recorded to ``llm_calls.jsonl``.
    """
    api_url = os.environ.get("DF_API_URL") or _chat_url(
        os.environ.get("DF_API_BASE_URL")
    )
    model = os.environ.get("DF_MODEL_NAME", "").strip()
    if not api_url or not model or not os.environ.get("DF_API_KEY"):
        raise OSError(
            "DF_API_KEY, DF_API_BASE_URL/DF_API_URL, and DF_MODEL_NAME are required"
        )
    serving_class = _load_text_serving_class()
    raw = serving_class(
        api_url=api_url,
        model_name=model,
        key_name_of_api_key="DF_API_KEY",
        temperature=0.0,
        max_workers=int(os.environ.get("DF_MAX_WORKERS", "8")),
    )
    from df_logging import LoggingLLMServing  # sibling runtime file, lazy import

    return LoggingLLMServing(raw)


def _load_text_serving_class() -> Any:
    """Load ``APILLMServing_request`` without importing ``dataflow.serving``.

    ``dataflow/serving/__init__.py`` pulls in transformers/torch, which can fail in
    the sandboxed platform env, so the concrete module is loaded by file path.
    """
    import dataflow  # top-level package is safe (no torch/transformers)

    existing = sys.modules.get("dataflow.serving.api_llm_serving_request")
    if existing is not None and hasattr(existing, "APILLMServing_request"):
        return existing.APILLMServing_request

    serving_dir = Path(dataflow.__file__).resolve().parent / "serving"
    package = sys.modules.get("dataflow.serving")
    if package is None:
        package = types.ModuleType("dataflow.serving")
        package.__path__ = [str(serving_dir)]
        package.__package__ = "dataflow.serving"
        sys.modules["dataflow.serving"] = package
    module_name = "dataflow.serving.api_llm_serving_request"
    spec = importlib.util.spec_from_file_location(
        module_name,
        serving_dir / "api_llm_serving_request.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError("could not load DataFlow APILLMServing_request")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.APILLMServing_request


def _retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES or status_code >= 500


def _response_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
        result = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Vision API response is missing message content.") from exc
    if not isinstance(result, str) or not result.strip():
        raise ValueError("Vision API returned empty message content.")
    return result.strip()


def _image_mime(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(path.suffix.lower(), "application/octet-stream")


def _log_vision_attempt(
    *,
    attempt: int,
    model: str,
    prompt: str,
    image_count: int,
    status: str,
    latency_ms: float,
    error: str | None = None,
) -> None:
    log_dir = os.environ.get("DF_LOG_DIR")
    if not log_dir:
        return
    path = Path(log_dir) / "llm_calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "seq": attempt,
        "attempt": attempt,
        "model": model,
        "status": status,
        "timestamp": datetime.now(_UTC).isoformat(),
        "latency_ms": round(latency_ms, 1),
        "request_payload": {
            "prompt": prompt[:1000],
            "image_count": image_count,
        },
        "error": _safe_error(error) if error else None,
    }
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(record, ensure_ascii=False) + "\n")


def _safe_error(value: str) -> str:
    api_key = os.environ.get("DF_API_KEY")
    if api_key:
        value = value.replace(api_key, "<redacted>")
    return value[:2000]


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)
