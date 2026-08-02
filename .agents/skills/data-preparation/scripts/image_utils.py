"""Managed DataFlow helpers for multi-image semantic labeling.

The module is uploaded beside an agent-authored pipeline.  It deliberately
delegates model transport and image encoding to DataFlow while owning the
small amount of adaptation required by the Pyromind training contract.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
import types
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import dataflow
import pandas as pd
from dataflow.core import OperatorABC
from dataflow.pipeline import StreamBatchedPipelineABC
from dataflow.utils.storage import StreamBatchedFileStorage
from preparation_runtime import write_progress


try:
    from datetime import UTC as _UTC  # Python 3.11+
except ImportError:
    from datetime import timezone

    _UTC = timezone.utc  # noqa: UP017 — Pyromind runs Python 3.10

try:
    from jsonschema import Draft202012Validator as _Draft202012Validator
except ImportError:  # DataFlow normally installs it transitively.
    _Draft202012Validator = None


SUPPORTED_DATAFLOW_VERSION = "1.0.10"
IMAGE_UTILS_API_VERSION = "1"
NATIVE_VLM_SUFFIXES = {".jpg", ".jpeg", ".png"}
CONVERTIBLE_VLM_SUFFIXES = {".gif", ".webp", ".bmp"}
IMAGE_SUFFIXES = NATIVE_VLM_SUFFIXES | CONVERTIBLE_VLM_SUFFIXES

__all__ = [
    "ImagePipelineConfig",
    "MultiImageSemanticLabelOperator",
    "run_image_pipeline",
    "run_image_pipeline_from_cli",
]


def _default_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string"},
            "answer": {"type": "string"},
        },
        "required": ["reasoning", "answer"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ImagePipelineConfig:
    """Declarative configuration for the managed image-labeling pipeline."""

    labeling_system_prompt: str
    training_system_prompt: str
    id_key: str = "id"
    images_key: str = "images"
    image_labels_key: str | None = "image_labels"
    user_prompt_key: str | None = "user_prompt"
    sample_system_prompt_key: str | None = None
    user_prompt_template: str | None = None
    response_json_schema: dict[str, Any] = field(
        default_factory=_default_response_schema
    )
    reasoning_key: str = "reasoning"
    answer_key: str = "answer"
    answer_is_json: bool = False
    batch_size: int = 8
    max_attempts: int = 3
    max_workers: int = 8
    timeout: int = 1800

    def __post_init__(self) -> None:
        for name in (
            "labeling_system_prompt",
            "training_system_prompt",
            "id_key",
            "images_key",
            "reasoning_key",
            "answer_key",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.user_prompt_key is None and self.user_prompt_template is None:
            raise ValueError(
                "user_prompt_key or user_prompt_template must be configured"
            )
        if not isinstance(self.response_json_schema, dict):
            raise ValueError("response_json_schema must be an object")
        if _Draft202012Validator is not None:
            try:
                _Draft202012Validator.check_schema(self.response_json_schema)
            except Exception as exc:
                raise ValueError(f"response_json_schema is invalid: {exc}") from exc
        for name in ("batch_size", "max_attempts", "max_workers", "timeout"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")


class ManagedStreamBatchedFileStorage(StreamBatchedFileStorage):
    """DataFlow streaming storage with atomically committed batch shards."""

    def __init__(
        self,
        *,
        first_entry_file_name: str,
        state_dir: Path,
        output_path: Path,
    ) -> None:
        self.state_dir = state_dir
        self.output_path = output_path
        self.parts_dir = state_dir / "processed.parts"
        super().__init__(
            first_entry_file_name=first_entry_file_name,
            cache_path=str(state_dir),
            file_name_prefix="image_pipeline",
            cache_type="jsonl",
        )

    @property
    def checkpoint_path(self) -> Path:
        return self.state_dir / "image_pipeline_last_success_step.txt"

    def reset_managed_state(self) -> None:
        self.checkpoint_path.unlink(missing_ok=True)
        self.output_path.unlink(missing_ok=True)
        if self.parts_dir.exists():
            shutil.rmtree(self.parts_dir)

    def checkpoint(self) -> tuple[int, int] | None:
        try:
            raw = self.checkpoint_path.read_text(encoding="utf-8").strip()
            operator_step, next_batch = (int(item) for item in raw.split(",", 1))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None
        return operator_step, next_batch

    def reconcile_parts(self) -> None:
        checkpoint = self.checkpoint()
        if checkpoint is None or not self.parts_dir.is_dir():
            return
        operator_step, next_batch = checkpoint
        if operator_step > 0:
            return
        for part in self.parts_dir.glob("part-*.jsonl"):
            try:
                part_index = int(part.stem.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            if part_index >= next_batch:
                part.unlink(missing_ok=True)

    def write(self, data: Any) -> str:
        dataframe = _as_dataframe(data)
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        target = self.parts_dir / f"part-{self.batch_step:08d}.jsonl"
        temporary = target.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            dataframe.to_json(
                handle,
                orient="records",
                lines=True,
                force_ascii=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return str(target)

    def materialize_output(self) -> int:
        self.reconcile_parts()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        record_count = 0
        with temporary.open("wb") as target:
            if self.parts_dir.is_dir():
                for part in sorted(self.parts_dir.glob("part-*.jsonl")):
                    payload = part.read_bytes()
                    target.write(payload)
                    record_count += sum(
                        1 for line in payload.splitlines() if line.strip()
                    )
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, self.output_path)
        return record_count


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.reset_index(drop=True)
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return pd.DataFrame(data)
    raise ValueError(f"unsupported DataFlow output type: {type(data).__name__}")


class MultiImageSemanticLabelOperator(OperatorABC):
    """Generate and validate canonical Pyromind rows for one DataFlow batch."""

    def __init__(
        self,
        config: ImagePipelineConfig,
        *,
        state_dir: Path,
        serving: Any | None = None,
    ) -> None:
        self.config = config
        self.state_dir = state_dir
        self.llm_serving = serving or _create_vlm_serving(config)
        self._progress_path = state_dir / "progress.json"
        self._total = self._read_total_records()
        self._processed = self._count_committed_records()
        self._attempted_this_run = 0
        self._started_at = time.monotonic()
        self._write_progress_snapshot()

    def run(self, storage: Any) -> None:
        dataframe = storage.read("dataframe")
        records = dataframe.to_dict(orient="records")
        prepared = [
            _prepare_sample(record, self.config, self.state_dir) for record in records
        ]
        responses = self._generate_validated(prepared)
        rows = [
            _canonical_output(sample, response, self.config)
            for sample, response in zip(prepared, responses, strict=True)
        ]
        storage.write(rows)
        self._processed += len(rows)
        self._attempted_this_run += len(rows)
        self._write_progress_snapshot()

    def _read_total_records(self) -> int:
        metadata_path = self.state_dir / "runtime_metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return 0
        total = metadata.get("record_count") if isinstance(metadata, dict) else None
        return total if isinstance(total, int) and total >= 0 else 0

    def _count_committed_records(self) -> int:
        parts_dir = self.state_dir / "processed.parts"
        if not parts_dir.is_dir():
            return 0
        count = 0
        for part in parts_dir.glob("part-*.jsonl"):
            try:
                with part.open("rb") as handle:
                    count += sum(1 for line in handle if line.strip())
            except OSError:
                continue
        return count

    def _write_progress_snapshot(self) -> None:
        write_progress(
            self._progress_path,
            total=self._total,
            processed=self._processed,
            succeeded=self._processed,
            failed=0,
            started_at=self._started_at,
            attempted_this_run=self._attempted_this_run,
        )

    def _generate_validated(
        self,
        samples: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any] | None] = [None] * len(samples)
        pending = list(range(len(samples)))
        prompts = [sample["_model_prompt"] for sample in samples]
        last_errors: dict[int, str] = {}

        for attempt in range(1, self.config.max_attempts + 1):
            request_indices = list(pending)
            try:
                raw_responses = self.llm_serving.generate_from_input_multi_images(
                    [samples[index]["_local_images"] for index in request_indices],
                    [samples[index]["_image_labels"] for index in request_indices],
                    system_prompt="",
                    user_prompts=[prompts[index] for index in request_indices],
                    timeout=self.config.timeout,
                    json_schema=self.config.response_json_schema,
                )
                if len(raw_responses) != len(request_indices):
                    raise ValueError(
                        "DataFlow VLM returned a response count that does not "
                        "match the request count"
                    )
            except Exception as exc:
                error = _safe_error(exc)
                for index in request_indices:
                    last_errors[index] = error
                    _log_attempt(
                        self.state_dir,
                        samples[index],
                        attempt=attempt,
                        status="error",
                        error=error,
                    )
                if attempt == self.config.max_attempts:
                    break
                continue

            pending = []
            for index, raw_response in zip(
                request_indices,
                raw_responses,
                strict=True,
            ):
                try:
                    parsed = _validate_response(raw_response, self.config)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    error = _safe_error(exc)
                    last_errors[index] = error
                    prompts[index] = (
                        f"{samples[index]['_model_prompt']}\n\n"
                        "The previous response failed validation: "
                        f"{error}. Return only a corrected JSON object."
                    )
                    pending.append(index)
                    _log_attempt(
                        self.state_dir,
                        samples[index],
                        attempt=attempt,
                        status="invalid_output",
                        error=error,
                    )
                    continue
                results[index] = parsed
                _log_attempt(
                    self.state_dir,
                    samples[index],
                    attempt=attempt,
                    status="success",
                )
            if not pending:
                return [value for value in results if value is not None]

        failed_index = pending[0] if pending else next(iter(last_errors), 0)
        failed = samples[failed_index]
        error = last_errors.get(failed_index, "unknown VLM failure")
        _write_failure(
            self.state_dir,
            failed,
            stage="image_label",
            error=error,
            attempts=self.config.max_attempts,
        )
        raise ValueError(
            f"image sample {failed['id']!r} failed after "
            f"{self.config.max_attempts} attempts: {error}"
        )


def run_image_pipeline(
    config: ImagePipelineConfig,
    input_path: str,
    output_path: str,
    limit: int | None = None,
) -> None:
    """Build and execute the managed one-operator DataFlow pipeline."""

    _require_supported_dataflow()
    source = Path(input_path).resolve()
    output = Path(output_path).resolve()
    state_dir = Path(os.environ.get("DF_STATE_DIR", output.parent)).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    resumed = os.environ.get("DF_RESUME") == "1"

    manifest = state_dir / "source_manifest.jsonl"
    try:
        if resumed:
            if not manifest.is_file():
                raise ValueError("cannot resume: source_manifest.jsonl is missing")
            _refresh_resume_metadata(state_dir, manifest, config)
        else:
            records, image_root = _load_source_records(source, config, limit)
            _write_jsonl_atomic(manifest, records)
            _write_runtime_metadata(
                state_dir,
                config=config,
                source=source,
                manifest=manifest,
                image_root=image_root,
                record_count=len(records),
            )
    except Exception as exc:
        _write_failure(
            state_dir,
            {"id": None, "_source_index": None, "_source_path": str(source)},
            stage="input_validation",
            error=_safe_error(exc),
            attempts=0,
        )
        raise

    storage = ManagedStreamBatchedFileStorage(
        first_entry_file_name=str(manifest),
        state_dir=state_dir,
        output_path=output,
    )
    if resumed:
        storage.reconcile_parts()
    else:
        storage.reset_managed_state()

    class _ManagedImagePipeline(StreamBatchedPipelineABC):
        def __init__(self) -> None:
            super().__init__()
            self.storage = storage
            self.label = MultiImageSemanticLabelOperator(
                config,
                state_dir=state_dir,
            )

        def forward(self) -> None:
            self.label.run(self.storage.step())

    pipeline: _ManagedImagePipeline | None = None
    try:
        pipeline = _ManagedImagePipeline()
        pipeline.compile()
        pipeline.forward(
            batch_size=config.batch_size,
            resume_from_last=True,
        )
    except Exception as exc:
        if not (state_dir / "failure.json").is_file():
            _write_failure(
                state_dir,
                {"id": None, "_source_index": None, "_source_path": str(source)},
                stage="pipeline",
                error=_safe_error(exc),
                attempts=0,
            )
        storage.materialize_output()
        raise
    else:
        storage.materialize_output()
        (state_dir / "failure.json").unlink(missing_ok=True)
    finally:
        serving = (
            getattr(pipeline.label, "llm_serving", None)
            if pipeline is not None
            else None
        )
        if serving is not None:
            try:
                serving.cleanup()
            except Exception:
                pass


def run_image_pipeline_from_cli(config: ImagePipelineConfig) -> None:
    """Run the standard ``input output [limit]`` command-line contract."""

    if len(sys.argv) not in {3, 4}:
        raise SystemExit("usage: pipeline.py <input_path> <processed.jsonl> [limit]")
    run_image_pipeline(
        config,
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3]) if len(sys.argv) == 4 else None,
    )


def _require_supported_dataflow() -> None:
    version = str(getattr(dataflow, "__version__", "")).strip()
    if version != SUPPORTED_DATAFLOW_VERSION:
        raise RuntimeError(
            "managed image pipelines require open-dataflow=="
            f"{SUPPORTED_DATAFLOW_VERSION}; found {version or 'unknown'}"
        )


def _create_vlm_serving(config: ImagePipelineConfig) -> Any:
    api_base = os.environ.get("DF_API_BASE_URL", "").strip()
    model = os.environ.get("DF_MODEL_NAME", "").strip()
    if not api_base or not model or not os.environ.get("DF_API_KEY"):
        raise OSError("DF_API_KEY, DF_API_BASE_URL, and DF_MODEL_NAME are required")
    serving_class = _load_vlm_class()
    return serving_class(
        api_url=api_base,
        key_name_of_api_key="DF_API_KEY",
        model_name=model,
        max_workers=config.max_workers,
        timeout=config.timeout,
    )


def _load_vlm_class() -> Any:
    existing = sys.modules.get("dataflow.serving.api_vlm_serving_openai")
    if existing is not None and hasattr(existing, "APIVLMServing_openai"):
        return existing.APIVLMServing_openai

    serving_dir = Path(dataflow.__file__).resolve().parent / "serving"
    package = sys.modules.get("dataflow.serving")
    if package is None:
        package = types.ModuleType("dataflow.serving")
        package.__path__ = [str(serving_dir)]
        package.__package__ = "dataflow.serving"
        sys.modules["dataflow.serving"] = package
    module_name = "dataflow.serving.api_vlm_serving_openai"
    spec = importlib.util.spec_from_file_location(
        module_name,
        serving_dir / "api_vlm_serving_openai.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError("could not load DataFlow APIVLMServing_openai")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.APIVLMServing_openai


def _load_source_records(
    source: Path,
    config: ImagePipelineConfig,
    limit: int | None,
) -> tuple[list[dict[str, Any]], Path]:
    if not source.exists():
        raise ValueError(f"input path does not exist: {source}")
    if source.is_dir():
        image_root = source
        records = _discover_directory(source, limit)
    else:
        image_root = source.parent
        records = _read_jsonl(source, limit)
    if not records:
        raise ValueError("image input contains no records")

    normalized = [
        _normalize_source_record(
            record,
            source_index=index,
            image_root=image_root,
            config=config,
        )
        for index, record in enumerate(records)
    ]
    return normalized, image_root


def _read_jsonl(path: Path, limit: int | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                raise ValueError(f"line {line_number}: blank lines are not allowed")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number}: record must be an object")
            records.append(value)
            if limit is not None and len(records) >= limit:
                break
    return records


def _discover_directory(path: Path, limit: int | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in sorted(path.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            images = [
                item.relative_to(path).as_posix()
                for item in sorted(entry.rglob("*"))
                if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
            ]
        elif entry.suffix.lower() in IMAGE_SUFFIXES:
            images = [entry.relative_to(path).as_posix()]
        else:
            continue
        if not images:
            continue
        records.append({"id": entry.relative_to(path).as_posix(), "images": images})
        if limit is not None and len(records) >= limit:
            break
    return records


def _normalize_source_record(
    record: dict[str, Any],
    *,
    source_index: int,
    image_root: Path,
    config: ImagePipelineConfig,
) -> dict[str, Any]:
    normalized = copy.deepcopy(record)
    record_id = record.get(config.id_key) or record.get("id") or record.get("sample_id")
    if not isinstance(record_id, (str, int)) or not str(record_id).strip():
        raise ValueError(f"record {source_index}: id must be non-empty")
    raw_images = (
        record.get(config.images_key)
        or record.get("images")
        or record.get("image_paths")
    )
    if raw_images is None and record.get("image_path"):
        raw_images = [record["image_path"]]
    if not isinstance(raw_images, list) or not raw_images:
        raise ValueError(f"record {source_index}: images must be a non-empty list")

    output_images: list[str] = []
    local_images: list[str] = []
    for raw_path in raw_images:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(
                f"record {source_index}: every image path must be non-empty"
            )
        posix = PurePosixPath(raw_path)
        if posix.is_absolute() or ".." in posix.parts or raw_path != posix.as_posix():
            raise ValueError(
                f"record {source_index}: image paths must be relative POSIX paths"
            )
        local = (image_root / Path(*posix.parts)).resolve()
        try:
            local.relative_to(image_root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"record {source_index}: image escapes input root"
            ) from exc
        if not local.is_file():
            raise ValueError(f"record {source_index}: missing image {raw_path}")
        if local.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(
                f"record {source_index}: unsupported image format {local.suffix}"
            )
        output_images.append(posix.as_posix())
        local_images.append(str(local))

    normalized["id"] = str(record_id).strip()
    normalized["images"] = output_images
    normalized["_local_images"] = local_images
    normalized["_source_index"] = source_index
    normalized["_source_path"] = str(record.get("source_path") or "")
    return normalized


def _prepare_sample(
    record: dict[str, Any],
    config: ImagePipelineConfig,
    state_dir: Path,
) -> dict[str, Any]:
    sample = dict(record)
    output_images = _as_string_list(sample.get("images"), "images")
    raw_local_images = _as_string_list(sample.get("_local_images"), "_local_images")
    local_images = [
        str(_prepare_vlm_image(Path(path), state_dir)) for path in raw_local_images
    ]

    labels: list[str]
    raw_labels = (
        sample.get(config.image_labels_key)
        if config.image_labels_key is not None
        else None
    )
    if raw_labels is None:
        labels = [f"Image {index}" for index in range(1, len(local_images) + 1)]
    else:
        labels = _as_string_list(raw_labels, config.image_labels_key or "image_labels")
        if len(labels) != len(local_images):
            raise ValueError("image labels must match the number of images")

    user_prompt = _resolve_user_prompt(sample, config)
    sample_system_prompt = config.labeling_system_prompt
    if config.sample_system_prompt_key:
        candidate = sample.get(config.sample_system_prompt_key)
        if isinstance(candidate, str) and candidate.strip():
            sample_system_prompt = candidate.strip()
    sample["_local_images"] = local_images
    sample["_image_labels"] = labels
    sample["_user_prompt"] = user_prompt
    sample["_model_prompt"] = f"{sample_system_prompt}\n\n{user_prompt}".strip()
    sample["images"] = output_images
    return sample


def _resolve_user_prompt(
    sample: dict[str, Any],
    config: ImagePipelineConfig,
) -> str:
    prompt_keys = [
        key
        for key in (config.user_prompt_key, "user_prompt", "prompt")
        if key is not None
    ]
    for key in dict.fromkeys(prompt_keys):
        value = sample.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if config.user_prompt_template:
        try:
            rendered = config.user_prompt_template.format_map(sample).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"user_prompt_template could not be rendered: {exc}"
            ) from exc
        if rendered:
            return rendered
    raise ValueError(f"sample {sample.get('id')!r} has no non-empty user prompt")


def _prepare_vlm_image(path: Path, state_dir: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix in NATIVE_VLM_SUFFIXES:
        return path
    if suffix not in CONVERTIBLE_VLM_SUFFIXES:
        raise ValueError(f"unsupported image format: {suffix}")
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to convert this image format") from exc

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = state_dir / "vlm_images" / f"{digest}.png"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".png.tmp")
    with Image.open(path) as image:
        image.seek(0)
        converted = image.convert("RGB")
        converted.save(temporary, format="PNG")
    os.replace(temporary, target)
    return target


def _validate_response(
    raw_response: Any,
    config: ImagePipelineConfig,
) -> dict[str, Any]:
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ValueError("VLM response must be a non-empty string")
    value = json.loads(raw_response)
    if not isinstance(value, dict):
        raise ValueError("VLM response must be a JSON object")
    _validate_json_schema(value, config.response_json_schema)
    _stringify(value.get(config.reasoning_key), config.reasoning_key)
    answer = _stringify(value.get(config.answer_key), config.answer_key)
    if config.answer_is_json:
        json.loads(answer)
    return value


def _validate_json_schema(value: Any, schema: dict[str, Any]) -> None:
    if _Draft202012Validator is not None:
        try:
            _Draft202012Validator(schema).validate(value)
        except Exception as exc:
            raise ValueError(f"VLM response does not match JSON Schema: {exc}") from exc
        return
    _validate_schema_subset(value, schema, path="$")


def _validate_schema_subset(value: Any, schema: Any, *, path: str) -> None:
    """Validate the common structured-output subset without extra dependencies."""

    if not isinstance(schema, dict):
        raise ValueError(f"JSON Schema at {path} must be an object")
    expected_type = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected_type, str) and not type_matches.get(expected_type, True):
        raise ValueError(f"{path} must have type {expected_type}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of the configured enum values")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must equal the configured constant")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            missing = [name for name in required if name not in value]
            if missing:
                raise ValueError(f"{path} is missing required fields: {missing}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"{path}.properties must be an object")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ValueError(f"{path} contains additional fields: {extra}")
        for name, child_schema in properties.items():
            if name in value:
                _validate_schema_subset(
                    value[name],
                    child_schema,
                    path=f"{path}.{name}",
                )
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema_subset(
                item,
                schema["items"],
                path=f"{path}[{index}]",
            )


def _canonical_output(
    sample: dict[str, Any],
    response: dict[str, Any],
    config: ImagePipelineConfig,
) -> dict[str, Any]:
    reasoning = _stringify(response.get(config.reasoning_key), config.reasoning_key)
    answer = _stringify(response.get(config.answer_key), config.answer_key)
    if config.answer_is_json:
        json.loads(answer)
    return {
        "id": sample["id"],
        "image_path": sample["images"][0],
        "images": sample["images"],
        "system_prompt": config.training_system_prompt,
        "user_prompt": sample["_user_prompt"],
        "gt": f"<think>{reasoning}</think>\n\n<answer>{answer}</answer>",
    }


def _stringify(value: Any, field_name: str) -> str:
    if isinstance(value, str):
        result = value.strip()
    elif value is None:
        result = ""
    else:
        result = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if not result:
        raise ValueError(f"{field_name} must be non-empty")
    return result


def _as_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    return [item.strip() for item in value]


def _write_runtime_metadata(
    state_dir: Path,
    *,
    config: ImagePipelineConfig,
    source: Path,
    manifest: Path,
    image_root: Path,
    record_count: int,
) -> None:
    payload = {
        "image_utils_api_version": IMAGE_UTILS_API_VERSION,
        "dataflow_version": str(getattr(dataflow, "__version__", "")),
        "runtime_fingerprint": os.environ.get("DF_RUNTIME_FINGERPRINT"),
        "batch_size": config.batch_size,
        "source_path": str(source),
        "image_root": str(image_root),
        "manifest_path": str(manifest),
        "manifest_fingerprint": _sha256_file(manifest),
        "record_count": record_count,
    }
    _write_json_atomic(state_dir / "runtime_metadata.json", payload)


def _refresh_resume_metadata(
    state_dir: Path,
    manifest: Path,
    config: ImagePipelineConfig,
) -> None:
    metadata_path = state_dir / "runtime_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise ValueError("cannot resume: runtime_metadata.json is missing") from exc
    if not isinstance(metadata, dict):
        raise ValueError("cannot resume: runtime metadata is invalid")
    expected_manifest = metadata.get("manifest_fingerprint")
    actual_manifest = _sha256_file(manifest)
    if expected_manifest != actual_manifest:
        raise ValueError("cannot resume: source Manifest fingerprint changed")
    original_batch_size = metadata.get("batch_size")
    if original_batch_size != config.batch_size:
        raise ValueError(
            "cannot resume with a different batch_size; submit a new full run"
        )
    metadata.update(
        {
            "image_utils_api_version": IMAGE_UTILS_API_VERSION,
            "dataflow_version": str(getattr(dataflow, "__version__", "")),
            "runtime_fingerprint": os.environ.get("DF_RUNTIME_FINGERPRINT"),
        }
    )
    _write_json_atomic(metadata_path, metadata)


def _log_attempt(
    state_dir: Path,
    sample: dict[str, Any],
    *,
    attempt: int,
    status: str,
    error: str | None = None,
) -> None:
    path = state_dir / "llm_calls.jsonl"
    record = {
        "seq": sample.get("_source_index"),
        "attempt": attempt,
        "model": os.environ.get("DF_MODEL_NAME"),
        "status": status,
        "timestamp": datetime.now(_UTC).isoformat(),
        "latency_ms": 0,
        "request_payload": {
            "id": sample.get("id"),
            "prompt": str(sample.get("_user_prompt") or "")[:1000],
            "image_count": len(sample.get("images") or []),
        },
        "error": error,
    }
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_failure(
    state_dir: Path,
    sample: dict[str, Any],
    *,
    stage: str,
    error: str,
    attempts: int,
) -> None:
    payload = {
        "status": "failed",
        "failure": {
            "source_index": sample.get("_source_index"),
            "source_id": sample.get("id"),
            "source_path": sample.get("_source_path"),
            "stage": stage,
            "error": error,
            "attempts": attempts,
        },
    }
    _write_json_atomic(state_dir / "failure.json", payload)


def _safe_error(error: BaseException | str) -> str:
    value = str(error)
    api_key = os.environ.get("DF_API_KEY")
    if api_key:
        value = value.replace(api_key, "<redacted>")
    return value[:2000]


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
